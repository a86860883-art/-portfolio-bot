"""
每日排程 - 週一至週五 台灣時間 05:30
推播：總覽燈號 Flex + 重點新聞 Flex
"""
import asyncio
import logging
from datetime import datetime
import pytz

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from sources.holdings_store import load_holdings
from sources.stocktwits import get_sentiment
from sources.news import get_news
from sources.sec_edgar import get_filings
from analyzers.technical import analyze_technicals
from notifier.line_push import push_text
from notifier.report_flex import (
    build_overview_flex, build_news_flex, push_flex
)

log = logging.getLogger(__name__)
TZ  = pytz.timezone("Asia/Taipei")


async def daily_healthcheck():
    log.info(f"每日健檢開始 {datetime.now(TZ):%Y-%m-%d %H:%M}")
    holdings = load_holdings()
    if not holdings:
        await push_text("每日健檢：尚無持股資料，請上傳嘉信 CSV 更新持股。")
        return

    tickers = [h["symbol"] for h in holdings]
    today   = datetime.now(TZ).strftime("%Y/%m/%d")

    try:
        technicals, news = await asyncio.gather(
            analyze_technicals(tickers),
            get_news(tickers),
        )

        # 推播總覽燈號卡片
        overview = build_overview_flex(holdings, technicals, today)
        await push_flex(overview, f"{today} 持股健檢總覽")
        log.info("總覽推播完成")

        # 推播重點新聞卡片
        await asyncio.sleep(1)
        news_flex = build_news_flex(news)
        await push_flex(news_flex, "持股重點新聞")
        log.info("新聞推播完成")

    except Exception as e:
        log.error(f"每日健檢失敗：{e}", exc_info=True)
        await push_text(f"每日健檢執行失敗：{type(e).__name__}")


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
    import asyncio, logging
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    asyncio.run(daily_healthcheck())
