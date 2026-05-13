"""
AI 分析報告產生器（含交易盲點健檢）
環境變數在函數內讀取，確保 load_dotenv() 已執行
"""
import logging
import os
from datetime import datetime
import httpx

log = logging.getLogger(__name__)

BLIND_SPOTS_NOTE = {
    "TSLA": "TSLA 受消息面影響極大，純技術指標對急跌預警有限；2022 最大回撤曾達 -68%，停損紀律比進場時機更重要。",
    "NVDA": "NVDA AI 題材驅動期間 RSI 超買訊號易誤發；主要漲幅常集中於少數月份，持有期過短易錯過爆發段。",
    "GOOGL": "GOOGL 技術面穩定，但監管/反壟斷事件會在技術面出現前造成急跌，是純技術分析盲區。",
    "GOOG":  "GOOGL 技術面穩定，但監管/反壟斷事件會在技術面出現前造成急跌，是純技術分析盲區。",
    "MU":    "MU 對記憶體庫存週期敏感，週期轉折點時指標準確度驟降；下行週期需優先控制持倉比例。",
    "INTC":  "INTC 處於結構性競爭劣勢，順勢做多策略與其長期走勢衝突；反彈訊號易被誤判為趨勢反轉。",
}


def _build_prompt(holdings, technicals, sentiment, news, filings) -> str:
    today = datetime.now().strftime("%Y/%m/%d")
    lines = [f"今天是 {today}，請用繁體中文產生持股健檢報告。\n"]

    for h in holdings:
        sym = h["symbol"]
        t   = technicals.get(sym, {})
        s   = sentiment.get(sym, {})
        n   = news.get(sym, [])
        f   = filings.get(sym, [])

        lines.append(f"## {sym}")
        lines.append(
            f"持股：{h['quantity']:,.0f} 股，市值 ${h['market_value']:,.0f}，"
            f"成本 ${h['cost_basis']:.2f}，未實現損益 ${h['unrealized_pl']:,.0f}"
        )
        if "error" not in t and t:
            lines.append(
                f"技術面：現價 ${t.get('price')}，RSI {t.get('rsi')}，"
                f"MA20 {t.get('ma20')}，MA50 {t.get('ma50')}"
            )
            lines.append(f"  布林帶 {t.get('bb_pct')}%，成交量 {t.get('vol_ratio')}x，"
                         f"距52週高 {t.get('pct_from_high')}%")
            lines.append(f"  訊號：{', '.join(t.get('signals', []))}")
        if s.get("total"):
            lines.append(f"社群：多頭 {s['bullish']} / 空頭 {s['bearish']}，分數 {s['score']}")
        if n:
            lines.append("近期新聞：")
            for item in n[:3]:
                lines.append(f"  - [{item['source']}] {item['title'][:80]}")
        if f:
            lines.append("SEC 申報：")
            for filing in f[:2]:
                lines.append(f"  - {filing['date']} {filing['form']} {filing.get('label','')}")
        blind = BLIND_SPOTS_NOTE.get(sym)
        if blind:
            lines.append(f"盲點提示：{blind}")
        lines.append("")

    lines.append("""
請根據以上資料產生每日持股健檢報告，每支股票包含：
1. 股票代號與現價
2. 技術面小結（1-2句）
3. 社群情緒（1句）
4. 重要新聞（最多2則，無則略）
5. 交易盲點提示（1句客觀提醒）
6. 操作建議（謹慎觀察/中性持有/注意風險）
結尾：整體市場情緒小結（2-3句）
最後加免責聲明。每支股票控制在10行以內。
""")
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
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 2048,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
    report = resp.json()["content"][0]["text"]
    log.info("AI 報告產生完成")
    return report
