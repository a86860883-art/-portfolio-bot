"""
交易監控器 - 每 60 秒輪詢嘉信訂單，偵測到新成交立即推播 LINE
獨立執行：python -m monitor.trade_monitor
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import httpx

from sources.schwab import _refresh_access_token, _load_token
from notifier.line_push import push_trade_alert, push_security_alert

log = logging.getLogger(__name__)

BASE_URL   = "https://api.schwabapi.com/trader/v1"
STATE_FILE = Path(os.environ.get("TRADE_STATE_FILE", "seen_orders.json"))
POLL_SEC   = int(os.environ.get("TRADE_POLL_SECONDS", "60"))


def _load_seen() -> set[str]:
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()


def _save_seen(seen: set[str]):
    STATE_FILE.write_text(json.dumps(list(seen)))


def _parse_trade(order: dict) -> dict | None:
    """將嘉信 order 物件轉換成通知用的 trade dict"""
    status = order.get("status", "")
    if status not in ("FILLED", "PARTIALLY_FILLED"):
        return None

    legs = order.get("orderLegCollection", [])
    if not legs:
        return None

    leg        = legs[0]
    instrument = leg.get("instrument", {})
    action     = leg.get("instruction", "BUY")   # BUY / SELL / BUY_TO_OPEN …
    action     = "BUY" if "BUY" in action else "SELL"

    quantity   = order.get("filledQuantity", leg.get("quantity", 0))
    price      = order.get("price") or order.get("stopPrice") or 0
    # 市價單用 orderActivityCollection 的成交均價
    activities = order.get("orderActivityCollection", [])
    if activities:
        exec_legs  = activities[0].get("executionLegs", [])
        if exec_legs:
            price = exec_legs[0].get("price", price)

    amount     = quantity * price

    # 來源判斷：enteredTime 來源欄位或 orderType
    # 嘉信 API 不直接標示「App/Web/API」，
    # 但 API 下的單 session 通常為 NORMAL，
    # 且沒有 complexOrderStrategyType。
    # 這裡保守標示為 UNKNOWN，讓使用者自行判斷。
    source = "UNKNOWN"

    filled_at_raw = order.get("closeTime") or order.get("enteredTime", "")
    try:
        dt = datetime.fromisoformat(filled_at_raw.replace("Z", "+00:00"))
        filled_at = dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        filled_at = filled_at_raw

    return {
        "symbol":     instrument.get("symbol", ""),
        "action":     action,
        "quantity":   quantity,
        "price":      price,
        "amount":     amount,
        "order_type": order.get("orderType", ""),
        "status":     status,
        "order_id":   str(order.get("orderId", "")),
        "filled_at":  filled_at,
        "source":     source,
    }


async def _fetch_recent_orders(access_token: str) -> list[dict]:
    """取得近 24 小時的訂單"""
    from_dt = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )
    to_dt = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    headers = {"Authorization": f"Bearer {access_token}"}
    orders  = []

    async with httpx.AsyncClient(timeout=15) as client:
        # 取帳戶清單
        resp = await client.get(f"{BASE_URL}/accounts/accountNumbers", headers=headers)
        resp.raise_for_status()
        accounts = resp.json()

        for acct in accounts:
            acct_hash = acct["hashValue"]
            resp = await client.get(
                f"{BASE_URL}/accounts/{acct_hash}/orders",
                headers=headers,
                params={"fromEnteredTime": from_dt, "toEnteredTime": to_dt, "status": "FILLED"},
            )
            if resp.status_code == 200:
                orders.extend(resp.json())

    return orders


async def poll_once():
    """執行一次輪詢，回傳本次新偵測到的交易數"""
    try:
        token        = _load_token()
        access_token = await _refresh_access_token(token["refresh_token"])
    except Exception as e:
        log.error(f"Token 更新失敗：{e}")
        return 0

    try:
        orders = await _fetch_recent_orders(access_token)
    except Exception as e:
        log.error(f"訂單查詢失敗：{e}")
        return 0

    seen    = _load_seen()
    new_cnt = 0

    for order in orders:
        order_id = str(order.get("orderId", ""))
        if order_id in seen:
            continue

        trade = _parse_trade(order)
        if trade:
            try:
                await push_trade_alert(trade)
                log.info(f"新交易通知：{trade['action']} {trade['symbol']} x{trade['quantity']}")
                new_cnt += 1
            except Exception as e:
                log.error(f"推播失敗：{e}")

        seen.add(order_id)

    _save_seen(seen)
    return new_cnt


async def run_monitor():
    """持續輪詢主迴圈"""
    log.info(f"交易監控啟動，每 {POLL_SEC} 秒輪詢一次")
    consecutive_errors = 0

    while True:
        try:
            new = await poll_once()
            if new:
                log.info(f"本次偵測到 {new} 筆新交易")
            consecutive_errors = 0
        except Exception as e:
            consecutive_errors += 1
            log.error(f"輪詢錯誤（第 {consecutive_errors} 次）：{e}")
            if consecutive_errors >= 5:
                await push_security_alert(
                    f"交易監控連續 {consecutive_errors} 次失敗，可能需要重新認證。\n錯誤：{e}"
                )
                consecutive_errors = 0

        await asyncio.sleep(POLL_SEC)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    asyncio.run(run_monitor())
