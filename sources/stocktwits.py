"""
StockTwits - 免費社群情緒偵測
不需要 API Key，公開端點直接使用
"""
import asyncio
import logging
import httpx

log = logging.getLogger(__name__)

BASE = "https://api.stocktwits.com/api/2"


async def _fetch_ticker(client: httpx.AsyncClient, symbol: str) -> dict:
    """取得單一 ticker 的社群情緒與熱門訊息"""
    try:
        resp = await client.get(
            f"{BASE}/streams/symbol/{symbol}.json",
            params={"limit": 30},
            timeout=10,
        )
        if resp.status_code == 429:
            log.warning(f"StockTwits rate limit，跳過 {symbol}")
            return {"symbol": symbol, "bullish": 0, "bearish": 0, "messages": []}
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning(f"StockTwits {symbol} 失敗：{e}")
        return {"symbol": symbol, "bullish": 0, "bearish": 0, "messages": []}

    messages = data.get("messages", [])
    bullish = sum(1 for m in messages if m.get("entities", {}).get("sentiment", {}).get("basic") == "Bullish")
    bearish = sum(1 for m in messages if m.get("entities", {}).get("sentiment", {}).get("basic") == "Bearish")

    # 取前 3 則有內容的訊息
    top_msgs = [
        m["body"][:120] for m in messages
        if m.get("body") and len(m["body"]) > 20
    ][:3]

    return {
        "symbol":   symbol,
        "bullish":  bullish,
        "bearish":  bearish,
        "total":    len(messages),
        "score":    round((bullish - bearish) / max(bullish + bearish, 1) * 100),
        "messages": top_msgs,
    }


async def get_sentiment(tickers: list[str]) -> dict[str, dict]:
    """
    回傳每個 ticker 的情緒摘要
    { "AAPL": { bullish, bearish, score, messages }, ... }
    """
    # 避免觸發 rate limit，每次請求間隔 0.5 秒
    async with httpx.AsyncClient() as client:
        results = {}
        for ticker in tickers:
            results[ticker] = await _fetch_ticker(client, ticker)
            await asyncio.sleep(0.5)

    log.info(f"StockTwits 情緒蒐集完成：{list(results.keys())}")
    return results
