"""
SEC EDGAR - 官方免費 API
偵測重大申報：8-K（重大事件）、4（內線交易）
"""
import asyncio
import logging
from datetime import date, timedelta
import httpx

log = logging.getLogger(__name__)

EDGAR_BASE   = "https://data.sec.gov"
HEADERS      = {"User-Agent": "PortfolioHealthcheckBot contact@example.com"}
IMPORTANT_8K = {
    "1.01": "重大合約",
    "1.02": "終止重大合約",
    "1.03": "破產/接管",
    "2.01": "重大資產收購/處分",
    "2.02": "財報結果",
    "2.06": "資產減損",
    "3.01": "上市地位異動",
    "4.01": "會計師異動",
    "5.02": "高管異動（CEO/CFO）",
    "7.01": "Regulation FD 揭露",
    "8.01": "其他重大事件",
}


async def _get_cik(client: httpx.AsyncClient, symbol: str) -> str | None:
    """ticker → CIK 編號"""
    try:
        resp = await client.get(
            f"{EDGAR_BASE}/submissions/",
            params={"action": "getcompany", "company": symbol, "type": "", "dateb": "", "owner": "include", "count": "1"},
            headers=HEADERS,
        )
        # 直接用 company_tickers.json 更可靠
        resp2 = await client.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=HEADERS,
        )
        resp2.raise_for_status()
        data = resp2.json()
        for entry in data.values():
            if entry.get("ticker", "").upper() == symbol.upper():
                return str(entry["cik_str"]).zfill(10)
    except Exception as e:
        log.warning(f"EDGAR CIK 查詢失敗 {symbol}：{e}")
    return None


async def _get_recent_filings(client: httpx.AsyncClient, cik: str, symbol: str) -> list[dict]:
    """取得近 14 天的重大申報"""
    try:
        resp = await client.get(
            f"{EDGAR_BASE}/submissions/CIK{cik}.json",
            headers=HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning(f"EDGAR filings 失敗 {symbol}：{e}")
        return []

    recent = data.get("filings", {}).get("recent", {})
    forms   = recent.get("form", [])
    dates   = recent.get("filingDate", [])
    accnums = recent.get("accessionNumber", [])

    cutoff = (date.today() - timedelta(days=14)).isoformat()
    results = []

    for form, filed, acc in zip(forms, dates, accnums):
        if filed < cutoff:
            break
        if form not in ("8-K", "4", "SC 13D", "SC 13G"):
            continue
        acc_fmt = acc.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_fmt}/{acc}.txt"
        entry = {
            "symbol": symbol,
            "form":   form,
            "date":   filed,
            "url":    f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type={form}&dateb=&owner=include&count=5",
        }
        if form == "8-K":
            entry["label"] = "重大事件申報"
        elif form == "4":
            entry["label"] = "內線人員交易申報"
        elif form in ("SC 13D", "SC 13G"):
            entry["label"] = "大股東持股異動（>5%）"
        results.append(entry)

    return results[:5]


async def get_filings(tickers: list[str]) -> dict[str, list[dict]]:
    """
    回傳每個 ticker 近 14 天的重大 SEC 申報
    { "AAPL": [ { form, date, label, url }, ... ] }
    """
    async with httpx.AsyncClient(timeout=20) as client:
        # 先批次取得所有 CIK
        cik_map = {}
        for ticker in tickers:
            cik = await _get_cik(client, ticker)
            if cik:
                cik_map[ticker] = cik
            await asyncio.sleep(0.2)  # EDGAR rate limit 友善

        # 取申報
        results = {}
        for ticker, cik in cik_map.items():
            filings = await _get_recent_filings(client, cik, ticker)
            results[ticker] = filings
            await asyncio.sleep(0.2)

    log.info(f"SEC EDGAR 申報蒐集完成")
    return results
