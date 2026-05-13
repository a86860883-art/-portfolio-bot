"""
每日排程 - 週一至週五 台灣時間 05:30 自動健檢推播
"""
import asyncio
import logging
import os
from datetime import datetime
import pytz

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from sources.holdings_store import load_holdings
from sources.stocktwits import get_sentiment
from sources.news import get_news
from sources.sec_edgar import get_filings
from analyzers.technical import analyze_technicals
from analyzers.ai_summary import generate_report
from notifier.line_push import push_report, push_text

log = logging.getLogger(__name__)
TZ  = pytz.timezone("Asia/Taipei")


async def daily_healthcheck():
    log.info(f"每日健檢開始 {datetime.now(TZ):%Y-%m-%d %H:%M}")
    holdings = load_holdings()
    if not holdings:
        await push_text("每日健檢：尚無持股資料，請先傳送截圖更新持股。")
        return
    tickers = [h["symbol"] for h in holdings]
    try:
        t, s, n, f = await asyncio.gather(
            analyze_technicals(tickers),
            get_sentiment(tickers),
            get_news(tickers),
            get_filings(tickers),
        )
        report = await generate_report(holdings, t, s, n, f)
        await push_report(report)
        log.info("每日健檢推播完成")
    except Exception as e:
        log.error(f"每日健檢失敗：{e}")
        await push_text(f"每日健檢執行失敗：{e}")


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(
        daily_healthcheck,
        CronTrigger(day_of_week="mon-fri", hour=5, minute=30, timezone=TZ),
        id="daily_healthcheck",
        replace_existing=True,
    )
    return scheduler


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    asyncio.run(daily_healthcheck())
