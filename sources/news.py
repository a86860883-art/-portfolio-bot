"""
新聞來源 - Yahoo Finance（免費，yfinance 套件）
補充：Google News RSS（完全免費，無需 API Key）
"""
import asyncio
import logging
import xml.etree.ElementTree as ET
import httpx
import yfinance as yf

log = logging.getLogger(__name__)


async def _yahoo_news(symbol: str) -> list[dict]:
    """yfinance 抓取 Yahoo Finance 新聞"""
    try:
        loop = asyncio.get_event_loop()
        ticker = await loop.run_in_executor(None, lambda: yf.Ticker(symbol))
        raw = await loop.run_in_executor(None, lambda: ticker.news or [])
        return [
            {
                "title":     n.get("content", {}).get("title", ""),
                "url":       n.get("content", {}).get("canonicalUrl", {}).get("url", ""),
                "publisher": n.get("content", {}).get("provider", {}).get("displayName", "Yahoo Finance"),
                "source":    "Yahoo Finance",
            }
            for n in raw[:5]
            if n.get("content", {}).get("title")
        ]
    except Exception as e:
        log.warning(f"Yahoo Finance {symbol} 失敗：{e}")
        return []


async def _google_news_rss(symbol: str) -> list[dict]:
    """
    Google News RSS — 完全免費，無需 API Key
    搜尋 "{SYMBOL} stock" 相關英文新聞
    """
    query = f"{symbol} stock"
    url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()

        root = ET.fromstring(resp.text)
        items = root.findall(".//item")[:5]
        news = []
        for item in items:
            title = item.findtext("title", "")
            link  = item.findtext("link", "")
            src   = item.findtext("source", "Google News")
            if title:
                news.append({"title": title, "url": link, "publisher": src, "source": "Google News"})
        return news
    except Exception as e:
        log.warning(f"Google News RSS {symbol} 失敗：{e}")
        return []


async def get_news(tickers: list[str]) -> dict[str, list[dict]]:
    """
    回傳每個 ticker 的新聞列表（Yahoo + Google News RSS 合併去重）
    { "AAPL": [ { title, url, publisher, source }, ... ] }
    """
    results = {}
    for ticker in tickers:
        yahoo, google = await asyncio.gather(
            _yahoo_news(ticker),
            _google_news_rss(ticker),
        )
        # 合併去重（以 title 前 40 字去重）
        seen = set()
        merged = []
        for n in yahoo + google:
            key = n["title"][:40]
            if key not in seen:
                seen.add(key)
                merged.append(n)
        results[ticker] = merged[:6]
        await asyncio.sleep(0.3)

    log.info(f"新聞蒐集完成")
    return results
