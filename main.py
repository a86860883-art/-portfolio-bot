"""
持股健檢 Bot - 主程式
每日美股收盤後自動執行健檢，推播 LINE 報告
"""
import asyncio
import logging
from datetime import datetime

from sources.schwab import get_holdings
from sources.stocktwits import get_sentiment
from sources.news import get_news
from sources.sec_edgar import get_filings
from analyzers.technical import analyze_technicals
from analyzers.ai_summary import generate_report
from notifier.line_push import push_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


async def run_healthcheck():
    log.info("=== 持股健檢開始 ===")

    # 1. 取得嘉信持股清單
    log.info("讀取嘉信持股...")
    holdings = await get_holdings()
    if not holdings:
        log.error("無法取得持股，中止執行")
        return
    tickers = [h["symbol"] for h in holdings]
    log.info(f"持股：{tickers}")

    # 2. 各 ticker 並行蒐集資料
    log.info("蒐集市場資料...")
    tasks = {
        "sentiment": get_sentiment(tickers),
        "news": get_news(tickers),
        "filings": get_filings(tickers),
        "technicals": analyze_technicals(tickers),
    }
    results = {}
    for key, coro in tasks.items():
        try:
            results[key] = await coro
        except Exception as e:
            log.warning(f"{key} 蒐集失敗：{e}")
            results[key] = {}

    # 3. Claude AI 統整分析
    log.info("AI 分析中...")
    report = await generate_report(
        holdings=holdings,
        technicals=results["technicals"],
        sentiment=results["sentiment"],
        news=results["news"],
        filings=results["filings"],
    )

    # 4. 推播 LINE
    log.info("推播 LINE...")
    await push_report(report)
    log.info("=== 健檢完成 ===")


if __name__ == "__main__":
    asyncio.run(run_healthcheck())
