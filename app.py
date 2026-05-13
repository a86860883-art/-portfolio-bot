from dotenv import load_dotenv
load_dotenv()

import asyncio, hashlib, hmac, base64, json, logging, os
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
import httpx

from scheduler.daily import create_scheduler
from sources.screenshot_ocr import extract_holdings_from_image
from sources.holdings_store import save_holdings, load_holdings, get_holdings_status
from sources.csv_import import parse_schwab_csv_bytes
from analyzers.technical import analyze_technicals
from analyzers.ai_summary import generate_report
from sources.stocktwits import get_sentiment
from sources.news import get_news
from sources.sec_edgar import get_filings
from notifier.line_push import push_report, push_text, reply_text
from notifier.report_flex import (
    build_overview_flex, build_detail_carousel,
    build_news_flex, build_holdings_pie_flex,
    build_sentiment_flex, push_flex, reply_flex
)

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

LINE_SECRET = os.environ["LINE_CHANNEL_SECRET"]
LINE_TOKEN  = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
ANTH_KEY    = os.environ["ANTHROPIC_API_KEY"]

conversation_histories: dict[str, list] = {}
# 暫存最後一次技術分析結果供第二階段使用
_last_technicals: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = create_scheduler()
    scheduler.start()
    log.info("每日排程已啟動（週一至五 05:30）")
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="持股健檢 Bot", lifespan=lifespan)


def verify_sig(body: bytes, sig: str) -> bool:
    digest = hmac.new(LINE_SECRET.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), sig)


async def download_file(msg_id: str) -> bytes:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"https://api-data.line.me/v2/bot/message/{msg_id}/content",
            headers={"Authorization": f"Bearer {LINE_TOKEN}"},
        )
        resp.raise_for_status()
        return resp.content


async def ask_claude(user_id: str, message: str) -> str:
    history = conversation_histories.setdefault(user_id, [])
    history.append({"role": "user", "content": message})
    if len(history) > 20:
        history[:] = history[-20:]
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTH_KEY,
                     "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 1024,
                "system": (
                    "你是一個專業的美股持股健檢助理，專精科技股分析。"
                    "用繁體中文回答，簡潔易讀，非投資建議。"
                ),
                "messages": history,
            },
        )
        resp.raise_for_status()
    reply = resp.json()["content"][0]["text"]
    history.append({"role": "assistant", "content": reply})
    return reply


# ── 背景任務 ──────────────────────────────────────────

async def process_overview_background():
    """第一階段：總覽燈號推播"""
    global _last_technicals
    try:
        holdings = load_holdings()
        if not holdings:
            await push_text("尚無持股資料，請先上傳嘉信 CSV 檔案")
            return
        tickers = [h["symbol"] for h in holdings]
        technicals = await analyze_technicals(tickers)
        _last_technicals = technicals
        today = datetime.now().strftime("%Y/%m/%d")
        flex  = build_overview_flex(holdings, technicals, today)
        await push_flex(flex, "持股健檢總覽")
        log.info("總覽推播完成")
    except Exception as e:
        log.error(f"總覽失敗：{e}", exc_info=True)
        await push_text(f"總覽產生失敗：{e}")


async def process_detail_background():
    """第二階段：個股詳細分析推播"""
    global _last_technicals
    try:
        holdings = load_holdings()
        if not holdings:
            await push_text("尚無持股資料，請先上傳 CSV")
            return
        technicals = _last_technicals
        if not technicals:
            tickers    = [h["symbol"] for h in holdings]
            technicals = await analyze_technicals(tickers)
            _last_technicals = technicals
        carousel = build_detail_carousel(holdings, technicals)
        await push_flex(carousel, "個股詳細分析")
        log.info("個股分析推播完成")
    except Exception as e:
        log.error(f"個股分析失敗：{e}", exc_info=True)
        await push_text(f"個股分析失敗：{e}")


async def process_sentiment_background():
    """社群情緒條狀圖推播"""
    try:
        holdings = load_holdings()
        if not holdings:
            await push_text("尚無持股資料，請先上傳 CSV")
            return
        tickers  = [h["symbol"] for h in holdings]
        sentiment = await get_sentiment(tickers)
        flex = build_sentiment_flex(sentiment)
        await push_flex(flex, "社群情緒分析")
        log.info("情緒條狀圖推播完成")
    except Exception as e:
        log.error(f"情緒推播失敗：{e}", exc_info=True)
        await push_text(f"社群情緒取得失敗：{e}")


async def process_news_background():
    """重點新聞推播"""
    try:
        holdings = load_holdings()
        if not holdings:
            await push_text("尚無持股資料，請先上傳 CSV")
            return
        tickers  = [h["symbol"] for h in holdings]
        news     = await get_news(tickers)
        flex     = build_news_flex(news)
        await push_flex(flex, "持股重點新聞")
        log.info("新聞推播完成")
    except Exception as e:
        log.error(f"新聞推播失敗：{e}", exc_info=True)
        await push_text(f"新聞取得失敗：{e}")


async def process_screenshot_background(msg_id: str):
    try:
        img      = await download_file(msg_id)
        holdings = await extract_holdings_from_image(img, "image/jpeg")
        if not holdings:
            await push_text("辨識失敗，請確認截圖是嘉信持股頁面，文字清晰無遮擋")
            return
        save_holdings(holdings)
        total = sum(h["market_value"] for h in holdings)
        lines = [f"截圖辨識成功！{len(holdings)} 筆持股\n"]
        for h in sorted(holdings, key=lambda x: -x["market_value"])[:8]:
            sign = "▲" if h["unrealized_pl"] >= 0 else "▼"
            lines.append(f"{h['symbol']:<6} ${h['market_value']:>8,.0f}  {sign}${abs(h['unrealized_pl']):,.0f}")
        lines += [f"\n總市值：${total:,.0f}", "按【今日總覽】開始健檢"]
        await push_text("\n".join(lines))
    except Exception as e:
        log.error(f"截圖辨識失敗：{e}", exc_info=True)
        await push_text(f"辨識失敗，請重新傳送截圖。\n（{type(e).__name__}）")


async def handle_csv_background(msg_id: str):
    try:
        data     = await download_file(msg_id)
        holdings = parse_schwab_csv_bytes(data)
        if not holdings:
            await push_text("CSV 解析失敗，請確認是嘉信持倉明細 CSV")
            return
        save_holdings(holdings, source="csv")
        total = sum(h["market_value"] for h in holdings)
        pl    = sum(h["unrealized_pl"] for h in holdings)
        sign  = "+" if pl >= 0 else ""
        lines = [f"CSV 匯入成功！{len(holdings)} 筆持股\n"]
        for h in sorted(holdings, key=lambda x: -x["market_value"])[:8]:
            s = "▲" if h["unrealized_pl"] >= 0 else "▼"
            lines.append(f"{h['symbol']:<6} ${h['market_value']:>8,.0f}  {s}${abs(h['unrealized_pl']):,.0f}")
        lines += [f"\n總市值：${total:,.0f}",
                  f"總損益：{sign}${abs(pl):,.0f}",
                  "按【今日總覽】開始健檢"]
        await push_text("\n".join(lines))
    except Exception as e:
        log.error(f"CSV 處理失敗：{e}", exc_info=True)
        await push_text(f"CSV 讀取失敗：{e}")


async def process_report_background():
    """舊版純文字報告（保留相容性）"""
    try:
        holdings = load_holdings()
        if not holdings:
            await push_text("尚無持股資料")
            return
        tickers = [h["symbol"] for h in holdings]
        t, s, n, f = await asyncio.gather(
            analyze_technicals(tickers), get_sentiment(tickers),
            get_news(tickers), get_filings(tickers),
        )
        await push_report(await generate_report(holdings, t, s, n, f))
    except Exception as e:
        log.error(f"報告失敗：{e}", exc_info=True)
        await push_text(f"報告產生失敗：{type(e).__name__}: {str(e)[:100]}")


HELP_TEXT = """📊 美股健檢機器人

【更新持股資料】
直接上傳嘉信 CSV 檔案給我
（Positions → Export 下載）
或傳送 App 截圖，Bot 自動辨識

【底部選單按鈕】
📊 今日總覽  — 全部持股燈號總覽
📈 個股分析  — 每檔股票詳細分析
🗞 重點新聞  — 3~5則持股相關新聞
💼 我的持股  — 目前持股清單
💬 社群情緒  — StockTwits 多空情緒
❓ 使用說明  — 顯示此說明

每日凌晨 5:30 自動推播總覽報告
有問題可直接用中文詢問"""


async def cmd_holdings() -> str:
    holdings = load_holdings()
    if not holdings:
        return "尚無持股資料，請上傳嘉信 CSV 檔案"
    lines = ["持股清單\n" + "─" * 30]
    total = 0.0
    for h in sorted(holdings, key=lambda x: -x["market_value"]):
        sign = "▲" if h["unrealized_pl"] >= 0 else "▼"
        lines.append(
            f"{h['symbol']:<6} {h['quantity']:>6,.0f}股"
            f"  ${h['market_value']:>9,.0f}"
            f"  {sign}${abs(h['unrealized_pl']):,.0f}"
        )
        total += h["market_value"]
    lines += ["─" * 30, f"總市值：${total:,.0f}"]
    return "\n".join(lines)


async def cmd_sentiment() -> str:
    holdings = load_holdings()
    if not holdings:
        return "尚無持股資料"
    sent  = await get_sentiment([h["symbol"] for h in holdings])
    lines = ["社群情緒（StockTwits）\n" + "─" * 24]
    for sym, s in sent.items():
        if not s.get("total"):
            lines.append(f"{sym}：無資料")
            continue
        mood = "偏多" if s["score"] > 20 else ("偏空" if s["score"] < -20 else "中性")
        lines.append(f"{sym}  {mood}  多{s['bullish']}/空{s['bearish']}  分數{s['score']}")
    return "\n".join(lines)


@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()
    sig  = request.headers.get("X-Line-Signature", "")
    if not verify_sig(body, sig):
        raise HTTPException(status_code=400, detail="Invalid signature")

    payload = json.loads(body)
    for event in payload.get("events", []):
        if event.get("type") != "message":
            continue

        msg_type    = event["message"].get("type")
        reply_token = event["replyToken"]
        user_id     = event["source"]["userId"]

        if msg_type == "file":
            filename = event["message"].get("fileName", "")
            if filename.lower().endswith(".csv"):
                await reply_text(reply_token, "讀取 CSV 中，完成後推播結果...")
                background_tasks.add_task(handle_csv_background, event["message"]["id"])
            else:
                await reply_text(reply_token, "請上傳嘉信 CSV 持倉明細（.csv 格式）")
            continue

        if msg_type == "image":
            await reply_text(reply_token, "辨識截圖中，完成後推播結果...")
            background_tasks.add_task(process_screenshot_background, event["message"]["id"])
            continue

        if msg_type != "text":
            continue

        text = event["message"]["text"].strip()

        if text in ("/overview", "今日總覽", "/report"):
            await reply_text(reply_token, "分析中，完成後推播總覽...")
            background_tasks.add_task(process_overview_background)

        elif text in ("/detail", "個股分析", "個股詳細分析"):
            await reply_text(reply_token, "整理個股資料中，完成後推播...")
            background_tasks.add_task(process_detail_background)

        elif text in ("/news", "重點新聞"):
            await reply_text(reply_token, "蒐集最新新聞中，完成後推播...")
            background_tasks.add_task(process_news_background)

        elif text in ("/holdings", "我的持股", "持股"):
            holdings = load_holdings()
            if not holdings:
                await reply_text(reply_token, "尚無持股資料，請上傳嘉信 CSV 檔案")
            else:
                flex = build_holdings_pie_flex(holdings)
                await reply_flex(reply_token, flex, "持股分布")

        elif text in ("/sentiment", "社群情緒", "情緒"):
            await reply_text(reply_token, "蒐集社群情緒中，完成後推播...")
            background_tasks.add_task(process_sentiment_background)

        elif text in ("/help", "help", "使用說明", "說明", "?"):
            await reply_text(reply_token, HELP_TEXT)

        elif text in ("/status", "狀態"):
            await reply_text(reply_token, get_holdings_status())

        elif text in ("/reset", "重置"):
            conversation_histories.pop(user_id, None)
            await reply_text(reply_token, "對話記憶已清除！")

        elif text in ("略過",):
            await reply_text(reply_token, "好的！明天凌晨 5:30 再自動推播。")

        else:
            try:
                reply = await ask_claude(user_id, text)
            except Exception as e:
                reply = f"AI 回覆失敗：{type(e).__name__}"
            await reply_text(reply_token, reply)

    return {"status": "ok"}


@app.get("/")
async def health():
    return {"status": "running", "time": datetime.now().isoformat()}


@app.get("/test")
async def test_api():
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTH_KEY,
                         "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": "claude-haiku-4-5-20251001",
                      "max_tokens": 10,
                      "messages": [{"role": "user", "content": "hi"}]},
            )
        return {"anthropic": "SUCCESS" if resp.status_code == 200
                else f"FAIL {resp.status_code}"}
    except Exception as e:
        return {"anthropic": f"FAIL: {e}"}
