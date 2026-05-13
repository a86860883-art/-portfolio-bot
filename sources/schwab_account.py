"""
嘉信 API 擴充 - 新增帳戶摘要（含槓桿計算所需資料）
加到原本 sources/schwab.py 的末尾
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
        raise FileNotFoundError("請先執行 python schwab_auth.py 完成認證")
    return json.loads(TOKEN_FILE.read_text())


async def _refresh_access_token(refresh_token: str) -> str:
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
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        )
        resp.raise_for_status()
        data = resp.json()
    token = _load_token()
    token["access_token"] = data["access_token"]
    if "refresh_token" in data:
        token["refresh_token"] = data["refresh_token"]
    TOKEN_FILE.write_text(json.dumps(token, indent=2))
    return data["access_token"]


async def _get_headers() -> dict:
    token = _load_token()
    access_token = await _refresh_access_token(token["refresh_token"])
    return {"Authorization": f"Bearer {access_token}"}


async def get_holdings() -> list[dict]:
    """持股清單（股票+ETF）"""
    try:
        headers = await _get_headers()
    except Exception as e:
        log.error(f"Schwab 認證失敗：{e}")
        return []

    async with httpx.AsyncClient(timeout=15) as client:
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
            for pos in data.get("securitiesAccount", {}).get("positions", []):
                inst = pos.get("instrument", {})
                if inst.get("assetType") not in ("EQUITY", "ETF"):
                    continue
                holdings.append({
                    "symbol":        inst.get("symbol", ""),
                    "quantity":      pos.get("longQuantity", 0),
                    "market_value":  pos.get("marketValue", 0),
                    "unrealized_pl": pos.get("currentDayProfitLoss", 0),
                    "cost_basis":    pos.get("averagePrice", 0),
                })
    return holdings


async def get_account_summary() -> dict:
    """
    取得帳戶資金摘要，用於計算槓桿率
    回傳：{ total_assets, net_assets, cash, margin_balance,
             unrealized_pl, day_pl, buying_power }
    """
    try:
        headers = await _get_headers()
    except Exception as e:
        log.error(f"Schwab 認證失敗：{e}")
        return {}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{BASE_URL}/accounts/accountNumbers", headers=headers)
        resp.raise_for_status()
        accounts = resp.json()

        summary = {
            "total_assets":   0.0,
            "net_assets":     0.0,
            "cash":           0.0,
            "margin_balance": 0.0,
            "unrealized_pl":  0.0,
            "day_pl":         0.0,
            "buying_power":   0.0,
        }

        for acct in accounts:
            acct_hash = acct["hashValue"]
            resp = await client.get(
                f"{BASE_URL}/accounts/{acct_hash}",
                headers=headers,
                params={"fields": "positions"},
            )
            resp.raise_for_status()
            data    = resp.json()
            acct_data = data.get("securitiesAccount", {})
            bal     = acct_data.get("currentBalances", {})
            init_bal = acct_data.get("initialBalances", {})

            # 嘉信各欄位對應
            # liquidationValue = 清算總值（≈ 總資產）
            # equity           = 淨值（總資產 - 融資負債）
            # cashBalance      = 現金
            # marginBalance    = 融資餘額（負數代表借款）
            # dayTradingBuyingPower / buyingPower

            liq      = bal.get("liquidationValue", 0)
            equity   = bal.get("equity", liq)
            cash     = bal.get("cashBalance", 0)
            margin   = bal.get("marginBalance", 0)       # 負值 = 融資借款
            buy_pow  = bal.get("buyingPower", 0)

            # 當日損益：從 positions 加總
            positions = acct_data.get("positions", [])
            day_pl   = sum(p.get("currentDayProfitLoss", 0) for p in positions)
            unreal_pl = sum(
                (p.get("marketValue", 0) - p.get("averagePrice", 0) * p.get("longQuantity", 0))
                for p in positions
            )

            summary["total_assets"]   += liq
            summary["net_assets"]     += equity
            summary["cash"]           += cash
            summary["margin_balance"] += abs(margin)   # 統一正數表示借款金額
            summary["day_pl"]         += day_pl
            summary["unrealized_pl"]  += unreal_pl
            summary["buying_power"]   += buy_pow

    # 槓桿率：由 dashboard.py 的 _calc_leverage() 計算
    log.info(
        f"帳戶摘要：總資產 ${summary['total_assets']:,.0f}，"
        f"淨值 ${summary['net_assets']:,.0f}，"
        f"融資 ${summary['margin_balance']:,.0f}"
    )
    return summary
