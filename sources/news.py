"""
新聞來源 - 兩大區塊：
1. 重大市場新聞（Fed、地緣政治、科技產業）3則以內
2. 持股相關新聞 5則以內
Claude AI 中文摘要，同公司當天最多1則，相似標題去重
"""
import asyncio
import difflib
import logging
import os
import re
import xml.etree.ElementTree as ET
import httpx

log = logging.getLogger(__name__)

MARKET_QUERIES = [
    "Federal Reserve interest rate policy",
    "US stock market economy major news",
    "semiconductor AI technology major news",
    "geopolitical war trade war US economy",
]


def _is_duplicate(title: str, seen_titles: list[str], threshold: float = 0.6) -> bool:
    """檢查標題是否與已見過的標題太相似（去重）"""
    t = title.lower()
    for s in seen_titles:
        ratio = difflib.SequenceMatcher(None, t, s.lower()).ratio()
        if ratio >= threshold:
            return True
    return False


async def _fetch_rss(query: str, max_items: int = 5) -> list[dict]:
    """抓取 Google News RSS"""
    url = (f"https://news.google.com/rss/search"
           f"?q={query}&hl=en-US&gl=US&ceid=US:en")
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            resp = await client.get(
                url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
        root  = ET.fromstring(resp.text)
        items = root.findall(".//item")[:max_items]
        news  = []
        for item in items:
            title = (item.findtext("title") or "").strip()
            link  = (item.findtext("link") or "").strip()
            src_el = item.find("source")
            src = (src_el.text.strip()
                   if src_el is not None and src_el.text else "Google News")
            pub_date = (item.findtext("pubDate") or "").strip()
            if title and not title.lower().startswith("google"):
                news.append({
                    "title": title, "url": link,
                    "publisher": src, "source": "Google News",
                    "pub_date": pub_date,
                })
        return news
    except Exception as e:
        log.warning(f"RSS {query[:30]}：{e}")
        return []


async def _translate_batch(items: list[dict], context: str = "") -> list[dict]:
    """批次翻譯 + 摘要，去掉重複新聞"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or not items:
        return items

    titles = "\n".join([
        f"{i+1}. {n['title'][:120]}"
        for i, n in enumerate(items)
    ])

    prompt = f"""以下是英文財經新聞標題{f'（關於 {context}）' if context else ''}。

請為每則新聞：
1. 翻譯成繁體中文標題（25字以內）
2. 寫一句80字以內的繁體中文說明（說明此新聞的重要性及對股市或個股的可能影響）

注意：若多則新聞描述同一事件（如同一公司高層賣股），請在說明中標注「與第N則相似」。

新聞：
{titles}

只回傳 JSON：
[
  {{"title_zh": "中文標題", "summary_zh": "中文說明", "is_duplicate": false}},
  ...
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
                    "max_tokens": 800,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            resp.raise_for_status()

        raw = resp.json()["content"][0]["text"].strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        summaries = json.loads(raw)

        result = []
        for i, n in enumerate(items):
            s = summaries[i] if i < len(summaries) else {}
            result.append({
                **n,
                "title_zh":    (s.get("title_zh") or "").strip(),
                "summary_zh":  (s.get("summary_zh") or "").strip(),
                "is_duplicate": s.get("is_duplicate", False),
            })
        return result
    except Exception as e:
        log.warning(f"翻譯失敗：{e}")
        return items


async def get_market_news() -> list[dict]:
    """取得重大市場新聞（最多3則，去重）"""
    all_news = []
    for q in MARKET_QUERIES:
        items = await _fetch_rss(q, max_items=3)
        all_news.extend(items)
        await asyncio.sleep(0.3)

    # 去重（標題相似度）
    seen_titles = []
    unique = []
    for n in all_news:
        if not _is_duplicate(n["title"], seen_titles, threshold=0.55):
            seen_titles.append(n["title"])
            unique.append(n)
        if len(unique) >= 4:
            break

    # 翻譯前3則
    top3 = unique[:3]
    if top3:
        top3 = await _translate_batch(top3, context="全球市場重大事件")

    log.info(f"市場新聞：{len(top3)} 則")
    return top3


async def get_stock_news(tickers: list[str]) -> list[dict]:
    """
    取得持股相關新聞（最多5則）
    同公司當天最多1則，相似標題去重
    """
    all_news   = []
    seen_syms  = {}   # { symbol: count }
    seen_titles = []

    for ticker in tickers:
        items = await _fetch_rss(f"{ticker} stock", max_items=4)
        for n in items:
            sym = ticker
            # 同公司當天最多1則
            if seen_syms.get(sym, 0) >= 1:
                continue
            # 標題去重
            if _is_duplicate(n["title"], seen_titles, threshold=0.55):
                continue
            seen_syms[sym] = seen_syms.get(sym, 0) + 1
            seen_titles.append(n["title"])
            all_news.append({**n, "symbol": sym})
            if len(all_news) >= 6:
                break
        if len(all_news) >= 6:
            break
        await asyncio.sleep(0.3)

    # 翻譯前5則
    top5 = all_news[:5]
    if top5:
        top5 = await _translate_batch(
            top5,
            context=f"持股 {', '.join(tickers[:5])}"
        )

    log.info(f"個股新聞：{len(top5)} 則")
    return top5


async def get_news(tickers: list[str]) -> dict:
    """
    統一入口，回傳兩區塊新聞
    { "market": [...], "stocks": [...] }
    """
    import json as _json
    global json
    json = _json

    market, stocks = await asyncio.gather(
        get_market_news(),
        get_stock_news(tickers),
    )
    return {"market": market, "stocks": stocks}
