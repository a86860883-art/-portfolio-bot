"""
StockTwits - 免費社群情緒偵測
"""
import asyncio
import logging
import httpx

log = logging.getLogger(__name__)
BASE = "https://api.stocktwits.com/api/2"

EMPTY = {"bullish": 0, "bearish": 0, "total": 0, "score": 0, "messages": []}


def _safe_get(obj, *keys, default=None):
    """安全多層取值，任何層是 None 都回傳 default"""
    for key in keys:
        if obj is None or not isinstance(obj, dict):
            return default
        obj = obj.get(key)
    return obj if obj is not None else default


async def _fetch_ticker(client: httpx.AsyncClient, symbol: str) -> dict:
    try:
        resp = await client.get(
            f"{BASE}/streams/symbol/{symbol}.json",
            params={"limit": 30}, timeout=10,
        )
        if resp.status_code != 200:
            return {"symbol": symbol, **EMPTY}
        data = resp.json()
    except Exception as e:
        log.warning(f"StockTwits {symbol} 失敗：{e}")
        return {"symbol": symbol, **EMPTY}

    raw = data.get("messages") or []
    messages = []
    for m in raw:
        try:
            if isinstance(m, dict):
                messages.append(m)
        except Exception:
            pass

    bullish, bearish = 0, 0
    for m in messages:
        try:
            sentiment = _safe_get(m, "entities", "sentiment", "basic")
            if sentiment == "Bullish":
                bullish += 1
            elif sentiment == "Bearish":
                bearish += 1
        except Exception:
            pass

    top_msgs = []
    for m in messages:
        try:
            body = m.get("body") or ""
            if len(body) > 20:
                top_msgs.append(body[:120])
            if len(top_msgs) >= 3:
                break
        except Exception:
            pass

    return {
        "symbol":   symbol,
        "bullish":  bullish,
        "bearish":  bearish,
        "total":    len(messages),
        "score":    round((bullish - bearish) / max(bullish + bearish, 1) * 100),
        "messages": top_msgs,
    }


async def get_sentiment(tickers: list[str]) -> dict[str, dict]:
    async with httpx.AsyncClient() as client:
        results = {}
        for ticker in tickers:
            try:
                results[ticker] = await _fetch_ticker(client, ticker)
            except Exception as e:
                log.warning(f"情緒蒐集 {ticker} 失敗：{e}")
                results[ticker] = {"symbol": ticker, **EMPTY}
            await asyncio.sleep(0.5)
    log.info(f"StockTwits 完成：{list(results.keys())}")
    return results
