"""
嘉信 API - 讀取持股（唯讀，不呼叫下單端點）
使用官方 schwab-py 套件
"""
import os
import json
import logging
from pathlib import Path
import httpx

log = logging.getLogger(__name__)

SCHWAB_APP_KEY    = os.environ["SCHWAB_APP_KEY"]
SCHWAB_APP_SECRET = os.environ["SCHWAB_APP_SECRET"]
TOKEN_FILE        = Path(os.environ.get("SCHWAB_TOKEN_FILE", "schwab_token.json"))
BASE_URL          = "https://api.schwabapi.com/trader/v1"


def _load_token() -> dict:
    if not TOKEN_FILE.exists():
        raise FileNotFoundError(
            f"找不到 {TOKEN_FILE}，請先執行 python schwab_auth.py 完成初次認證"
        )
    return json.loads(TOKEN_FILE.read_text())


async def _refresh_access_token(refresh_token: str) -> str:
    """用 refresh token 換新 access token（自動每 30 分鐘更新）"""
    import base64
    credentials = base64.b64encode(
        f"{SCHWAB_APP_KEY}:{SCHWAB_APP_SECRET}".encode()
    ).decode()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.schwabapi.com/v1/oauth/token",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    # 更新本地 token 檔
    token = _load_token()
    token["access_token"] = data["access_token"]
    if "refresh_token" in data:
        token["refresh_token"] = data["refresh_token"]
    TOKEN_FILE.write_text(json.dumps(token, indent=2))

    return data["access_token"]


async def get_holdings() -> list[dict]:
    """
    回傳持股清單，每筆包含：
    { symbol, quantity, market_value, unrealized_pl, cost_basis }
    """
    try:
        token = _load_token()
        access_token = await _refresh_access_token(token["refresh_token"])
    except Exception as e:
        log.error(f"Schwab 認證失敗：{e}")
        return []

    headers = {"Authorization": f"Bearer {access_token}"}

    async with httpx.AsyncClient(timeout=15) as client:
        # 取得帳戶列表（含 hash）
        resp = await client.get(f"{BASE_URL}/accounts/accountNumbers", headers=headers)
        resp.raise_for_status()
        accounts = resp.json()

        holdings = []
        for acct in accounts:
            acct_hash = acct["hashValue"]
            resp = await client.get(
                f"{BASE_URL}/accounts/{acct_hash}",
                headers=headers,
                params={"fields": "positions"},
            )
            resp.raise_for_status()
            data = resp.json()

            positions = data.get("securitiesAccount", {}).get("positions", [])
            for pos in positions:
                instrument = pos.get("instrument", {})
                asset_type = instrument.get("assetType", "")
                # 只取股票和 ETF（排除現金部位）
                if asset_type not in ("EQUITY", "ETF"):
                    continue
                holdings.append({
                    "symbol":        instrument.get("symbol", ""),
                    "quantity":      pos.get("longQuantity", 0),
                    "market_value":  pos.get("marketValue", 0),
                    "unrealized_pl": pos.get("currentDayProfitLoss", 0),
                    "cost_basis":    pos.get("averagePrice", 0),
                })

    log.info(f"取得 {len(holdings)} 筆持股")
    return holdings
