"""
新聞來源 - Google News RSS + Yahoo Finance
Claude AI 翻譯成繁體中文標題 + 100字精華摘要
"""
import asyncio
import logging
import os
import re
import xml.etree.ElementTree as ET
import httpx
import yfinance as yf

log = logging.getLogger(__name__)


async def _google_news_rss(symbol: str) -> list[dict]:
    """Google News RSS - 最穩定的免費來源"""
    url = (f"https://news.google.com/rss/search"
           f"?q={symbol}+stock&hl=en-US&gl=US&ceid=US:en")
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            resp = await client.get(
                url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
        root  = ET.fromstring(resp.text)
        items = root.findall(".//item")[:6]
        news  = []
        for item in items:
            title = (item.findtext("title") or "").strip()
            link  = (item.findtext("link") or "").strip()
            src_el = item.find("source")
            src = (src_el.text.strip()
                   if src_el is not None and src_el.text else "Google News")
            if title and not title.startswith("Google"):
                news.append({
                    "title": title, "url": link,
                    "publisher": src, "source": "Google News"
                })
        return news
    except Exception as e:
        log.warning(f"Google News {symbol}：{e}")
        return []


async def _yahoo_news(symbol: str) -> list[dict]:
    """Yahoo Finance 新聞"""
    try:
        loop = asyncio.get_event_loop()
        ticker = await loop.run_in_executor(None, lambda: yf.Ticker(symbol))
        raw    = await loop.run_in_executor(None, lambda: ticker.news or [])
        return [
            {
                "title":     (n.get("content", {}).get("title") or "").strip(),
                "url":       n.get("content", {}).get("canonicalUrl", {}).get("url", ""),
                "publisher": n.get("content", {}).get("provider", {}).get("displayName", "Yahoo"),
                "source":    "Yahoo Finance",
            }
            for n in raw[:5]
            if (n.get("content", {}).get("title") or "").strip()
        ]
    except Exception as e:
        log.warning(f"Yahoo {symbol}：{e}")
        return []


async def _translate_and_summarize(symbol: str, raw_news: list[dict]) -> list[dict]:
    """
    用 Claude Haiku 把英文新聞翻成繁體中文，並產生精華摘要
    每次只處理前3則，節省 Token（約 200-300 tokens/次）
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or not raw_news:
        return raw_news

    top3   = raw_news[:3]
    titles = "\n".join([
        f"{i+1}. {n['title'][:120]}"
        for i, n in enumerate(top3)
    ])

    prompt = f"""以下是 {symbol} 的英文財經新聞標題。請針對每則：
1. 翻譯成繁體中文標題（25字以內，精準傳達重點）
2. 寫一句繁體中文說明（80字以內，說明此新聞對 {symbol} 股價的可能影響或重要性）

新聞標題：
{titles}

只回傳 JSON，格式如下：
[
  {{"title_zh": "中文標題", "summary_zh": "中文說明"}},
  {{"title_zh": "中文標題", "summary_zh": "中文說明"}},
  {{"title_zh": "中文標題", "summary_zh": "中文說明"}}
]"""

    try:
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 600,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            resp.raise_for_status()

        import json
        raw = resp.json()["content"][0]["text"].strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        summaries = json.loads(raw)

        result = []
        for i, n in enumerate(top3):
            if i < len(summaries) and isinstance(summaries[i], dict):
                result.append({
                    **n,
                    "title_zh":   (summaries[i].get("title_zh") or "").strip(),
                    "summary_zh": (summaries[i].get("summary_zh") or "").strip(),
                })
            else:
                result.append(n)
        log.info(f"{symbol} 新聞中文摘要完成")
        return result

    except Exception as e:
        log.warning(f"{symbol} Claude 摘要失敗：{e}")
        return raw_news


async def get_news(tickers: list[str]) -> dict[str, list[dict]]:
    """回傳每個 ticker 的新聞（含繁體中文標題和摘要）"""
    results = {}
    for ticker in tickers:
        google, yahoo = await asyncio.gather(
            _google_news_rss(ticker),
            _yahoo_news(ticker),
        )
        # 合併去重，Google News 優先（較新）
        seen, merged = set(), []
        for n in google + yahoo:
            key = (n.get("title") or "")[:40]
            if key and key not in seen and len(key) > 5:
                seen.add(key)
                merged.append(n)

        top3 = merged[:3]

        # 加中文摘要
        if top3:
            top3 = await _translate_and_summarize(ticker, top3)

        results[ticker] = top3
        await asyncio.sleep(0.5)

    log.info("新聞蒐集完成（含中文摘要）")
    return results
