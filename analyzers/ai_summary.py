"""
AI 分析報告產生器 - Claude (Anthropic)
"""
import logging, os
from datetime import datetime
import httpx

log = logging.getLogger(__name__)

BLIND_SPOTS = {
    "TSLA": "TSLA 受消息面影響極大，停損紀律比進場時機更重要。",
    "NVDA": "NVDA 題材驅動期間 RSI 超買訊號易誤發，主要漲幅常集中於少數月份。",
    "GOOGL": "GOOGL 技術面穩定，但監管事件會在技術面出現前造成急跌。",
    "GOOG":  "GOOGL 技術面穩定，但監管事件會在技術面出現前造成急跌。",
    "MU":    "MU 對記憶體週期敏感，週期轉折點時指標準確度驟降。",
    "INTC":  "INTC 處於結構性競爭劣勢，順勢做多策略與其長期走勢衝突。",
}


def _build_prompt(holdings, technicals, sentiment, news, filings) -> str:
    today = datetime.now().strftime("%Y/%m/%d")
    lines = [f"今天是 {today}，請用繁體中文產生持股健檢報告。\n"]
    for h in holdings:
        sym = h["symbol"]
        t = technicals.get(sym, {})
        s = sentiment.get(sym, {})
        n = news.get(sym, [])
        f = filings.get(sym, [])
        lines.append(f"## {sym}")
        lines.append(f"持股：{h['quantity']:,.0f} 股，市值 ${h['market_value']:,.0f}，成本 ${h['cost_basis']:.2f}，損益 ${h['unrealized_pl']:,.0f}")
        if "error" not in t and t:
            lines.append(f"技術：現價 ${t.get('price')}，RSI {t.get('rsi')}，MA20 {t.get('ma20')}，MA50 {t.get('ma50')}")
            lines.append(f"  布林帶 {t.get('bb_pct')}%，成交量 {t.get('vol_ratio')}x，距52週高 {t.get('pct_from_high')}%")
            lines.append(f"  訊號：{', '.join(t.get('signals', []))}")
        if s.get("total"):
            lines.append(f"社群：多{s['bullish']}/空{s['bearish']}，分數 {s['score']}")
        if n:
            for item in n[:2]:
                lines.append(f"新聞：[{item['source']}] {item['title'][:70]}")
        if f:
            for filing in f[:1]:
                lines.append(f"SEC：{filing['date']} {filing['form']} {filing.get('label','')}")
        if sym in BLIND_SPOTS:
            lines.append(f"盲點：{BLIND_SPOTS[sym]}")
        lines.append("")
    lines.append("請產生每日健檢報告，每股包含：技術面小結、情緒、新聞、盲點提示、操作建議。結尾加整體市場小結和免責聲明。")
    return "\n".join(lines)


async def generate_report(holdings, technicals, sentiment, news, filings) -> str:
    api_key = os.environ["ANTHROPIC_API_KEY"]
    prompt  = _build_prompt(holdings, technicals, sentiment, news, filings)
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 2048,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
    return resp.json()["content"][0]["text"]
