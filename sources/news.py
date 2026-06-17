"""
新聞來源 - 三大區塊：
1. 國際重大事件（2則）  - 不考慮持股，以全球新聞重要性為準
2. 市場熱度個股（2則）  - 不考慮持股，以當日市場熱度為準
3. 持股相關新聞（3-5則）- 按持股市值加權 + 新聞時效性排序，同公司不重複

Claude AI 中文摘要，相似標題去重
"""
import asyncio
import difflib
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

log = logging.getLogger(__name__)

# ── 查詢設定 ──────────────────────────────────────────────

# 區塊一：廣泛的 top headlines，讓當日最重要新聞自然浮現，不限主題
GLOBAL_TOP_QUERIES = [
    "world news today breaking",
    "global economy news today",
    "US international news today",
]

# 區塊二：市場熱度個股，鎖定當日最受關注的股票/市場事件
MARKET_TRENDING_QUERIES = [
    "stocks trending today most active",
    "stock market biggest movers today",
    "IPO earnings stock news today",
]


# ── 工具函式 ──────────────────────────────────────────────

def _is_duplicate(title: str, seen_titles: list[str], threshold: float = 0.6) -> bool:
    t = title.lower()
    for s in seen_titles:
        if difflib.SequenceMatcher(None, t, s.lower()).ratio() >= threshold:
            return True
    return False


def _parse_pub_date(pub_date_str: str) -> datetime:
    """將 RSS pubDate 解析為 UTC datetime，失敗時回傳最舊時間（排序用）"""
    try:
        return parsedate_to_datetime(pub_date_str).astimezone(timezone.utc)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


async def _fetch_rss(query: str, max_items: int = 5) -> list[dict]:
    """抓取 Google News RSS，回傳含 pub_dt（datetime 物件）的列表"""
    url = (
        f"https://news.google.com/rss/search"
        f"?q={query}&hl=en-US&gl=US&ceid=US:en"
    )
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
        root = ET.fromstring(resp.text)
        items = root.findall(".//item")[:max_items]
        news = []
        for item in items:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            src_el = item.find("source")
            src = (
                src_el.text.strip()
                if src_el is not None and src_el.text
                else "Google News"
            )
            pub_date_str = (item.findtext("pubDate") or "").strip()
            pub_dt = _parse_pub_date(pub_date_str)
            if title and not title.lower().startswith("google"):
                news.append(
                    {
                        "title": title,
                        "url": link,
                        "publisher": src,
                        "source": "Google News",
                        "pub_date": pub_date_str,
                        "pub_dt": pub_dt,
                    }
                )
        return news
    except Exception as e:
        log.warning(f"RSS {query[:40]}：{e}")
        return []


async def _translate_batch(items: list[dict], context: str = "") -> list[dict]:
    """批次翻譯 + 摘要"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or not items:
        return items

    titles = "\n".join([f"{i+1}. {n['title'][:120]}" for i, n in enumerate(items)])

    prompt = f"""以下是英文財經新聞標題{f'（{context}）' if context else ''}。

請為每則新聞：
1. 翻譯成繁體中文標題（25字以內）
2. 寫一句80字以內的繁體中文說明（說明此新聞的重要性及對市場或個股的可能影響）

注意：若多則新聞描述同一事件，請在說明中標注「與第N則相似」。

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
            result.append(
                {
                    **n,
                    "title_zh": (s.get("title_zh") or "").strip(),
                    "summary_zh": (s.get("summary_zh") or "").strip(),
                    "is_duplicate": s.get("is_duplicate", False),
                }
            )
        return result
    except Exception as e:
        log.warning(f"翻譯失敗：{e}")
        return items


# ── 區塊一：國際重大事件 ──────────────────────────────────

async def get_global_news() -> list[dict]:
    """
    取得國際重大事件（2則）。
    不受持股影響，廣泛抓取 top headlines，以發布時效 + 去重篩選。
    """
    all_news = []
    for q in GLOBAL_TOP_QUERIES:
        items = await _fetch_rss(q, max_items=5)
        all_news.extend(items)
        await asyncio.sleep(0.3)

    # 依發布時間排序（最新優先），再去重
    all_news.sort(key=lambda x: x["pub_dt"], reverse=True)

    seen_titles: list[str] = []
    unique: list[dict] = []
    for n in all_news:
        if not _is_duplicate(n["title"], seen_titles, threshold=0.55):
            seen_titles.append(n["title"])
            unique.append(n)
        if len(unique) >= 4:
            break

    top2 = unique[:2]
    if top2:
        top2 = await _translate_batch(top2, context="國際重大事件")

    log.info(f"國際新聞：{len(top2)} 則")
    return top2


# ── 區塊二：市場熱度個股 ──────────────────────────────────

async def get_trending_stock_news() -> list[dict]:
    """
    取得市場熱度最高的個股新聞（2則）。
    不受持股影響，鎖定當日最活躍/最受關注標的。
    """
    all_news = []
    for q in MARKET_TRENDING_QUERIES:
        items = await _fetch_rss(q, max_items=5)
        all_news.extend(items)
        await asyncio.sleep(0.3)

    # 依發布時間排序後去重
    all_news.sort(key=lambda x: x["pub_dt"], reverse=True)

    seen_titles: list[str] = []
    unique: list[dict] = []
    for n in all_news:
        if not _is_duplicate(n["title"], seen_titles, threshold=0.55):
            seen_titles.append(n["title"])
            unique.append(n)
        if len(unique) >= 4:
            break

    top2 = unique[:2]
    if top2:
        top2 = await _translate_batch(top2, context="當日市場熱度最高個股")

    log.info(f"市場熱度新聞：{len(top2)} 則")
    return top2


# ── 區塊三：持股相關新聞 ──────────────────────────────────

async def get_stock_news(
    tickers: list[str],
    holdings: list[dict] | None = None,
) -> list[dict]:
    """
    取得持股相關新聞（3-5則）。
    - 以持股市值由大到小排序查詢順序（市值大的持股優先）
    - 每筆查詢取最新2則（pub_dt 最近）
    - 同公司當天最多1則，標題去重
    - 最終再依 pub_dt 排序，取前5
    """
    if not tickers:
        return []

    # 建立市值對照表，讓大倉位優先
    mv_map: dict[str, float] = {}
    if holdings:
        for h in holdings:
            mv_map[h["symbol"]] = h.get("market_value", 0)

    # 按市值由大到小排序 tickers
    sorted_tickers = sorted(
        tickers,
        key=lambda sym: mv_map.get(sym, 0),
        reverse=True,
    )

    candidates: list[dict] = []
    seen_syms: dict[str, int] = {}
    seen_titles: list[str] = []

    for ticker in sorted_tickers:
        items = await _fetch_rss(f"{ticker} stock news", max_items=4)

        # 只保留該 ticker 最新的前2則（時效性）
        items.sort(key=lambda x: x["pub_dt"], reverse=True)
        fresh = items[:2]

        for n in fresh:
            if seen_syms.get(ticker, 0) >= 1:
                continue
            if _is_duplicate(n["title"], seen_titles, threshold=0.55):
                continue
            seen_syms[ticker] = seen_syms.get(ticker, 0) + 1
            seen_titles.append(n["title"])
            candidates.append({**n, "symbol": ticker})

        await asyncio.sleep(0.3)

    # 最終依 pub_dt 再排序（較新的優先），取前5
    candidates.sort(key=lambda x: x["pub_dt"], reverse=True)
    top5 = candidates[:5]

    if top5:
        syms_ctx = ", ".join(dict.fromkeys(n["symbol"] for n in top5))
        top5 = await _translate_batch(top5, context=f"持股 {syms_ctx}")

    log.info(f"個股新聞：{len(top5)} 則")
    return top5


# ── 統一入口 ──────────────────────────────────────────────

async def get_news(
    tickers: list[str],
    holdings: list[dict] | None = None,
) -> dict:
    """
    統一入口，回傳三區塊新聞：
    {
        "global":   [...],   # 國際重大事件 2則
        "trending": [...],   # 市場熱度個股 2則
        "stocks":   [...],   # 持股相關新聞 3-5則
    }

    holdings 格式（選填，用於持股市值排序）：
        [{"symbol": "AAPL", "market_value": 50000}, ...]
    """
    global_news, trending_news, stock_news = await asyncio.gather(
        get_global_news(),
        get_trending_stock_news(),
        get_stock_news(tickers, holdings),
    )
    return {
        "global": global_news,
        "trending": trending_news,
        "stocks": stock_news,
    }
