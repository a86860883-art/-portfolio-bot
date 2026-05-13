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
from notifier.line_push import push_text, reply_text
from notifier.report_flex import (
    build_overview_flex, build_detail_carousel,
    build_news_flex, build_holdings_pie_flex,
    build_sentiment_flex, build_success_flex,
    build_status_flex, build_help_flex, build_clear_flex,
    push_flex, reply_flex
)
from notifier.chart_image import (
    generate_pie_chart, push_pie_chart,
    reply_pie_chart, _chart_cache
)
from fastapi.responses import Response

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")

LINE_SECRET = os.environ["LINE_CHANNEL_SECRET"]
LINE_TOKEN  = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
ANTH_KEY    = os.environ["ANTHROPIC_API_KEY"]

conversation_histories: dict[str, list] = {}
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


# ── 背景任務 ──────────────────────────────────────

async def process_overview_background():
    global _last_technicals
    try:
        holdings = load_holdings()
        if not holdings:
            await push_text("尚無持股資料，請先上傳嘉信 CSV 檔案")
            return
        technicals = await analyze_technicals([h["symbol"] for h in holdings])
        _last_technicals = technicals
        today = datetime.now().strftime("%Y/%m/%d")
        await push_flex(build_overview_flex(holdings, technicals, today), "持股健檢總覽")
    except Exception as e:
        log.error(f"總覽失敗：{e}", exc_info=True)
        await push_text(f"總覽產生失敗：{type(e).__name__}")


async def process_detail_background():
    global _last_technicals
    try:
        holdings = load_holdings()
        if not holdings:
            await push_text("尚無持股資料，請先上傳 CSV")
            return
        if not _last_technicals:
            _last_technicals = await analyze_technicals(
                [h["symbol"] for h in holdings])
        await push_flex(
            build_detail_carousel(holdings, _last_technicals), "個股詳細分析")
    except Exception as e:
        log.error(f"個股分析失敗：{e}", exc_info=True)
        await push_text(f"個股分析失敗：{type(e).__name__}")


async def process_news_background():
    try:
        holdings = load_holdings()
        if not holdings:
            await push_text("尚無持股資料，請先上傳 CSV")
            return
        news = await get_news([h["symbol"] for h in holdings])
        try:
            await push_flex(build_news_flex(news), "持股重點新聞")
        except Exception as flex_err:
            log.warning(f"新聞 Flex 失敗，改文字：{flex_err}")
            lines = ["🗞 持股重點新聞\n"]
            seen  = set()
            for sym, news_list in news.items():
                for n in (news_list or []):
                    t = (n.get("title_zh") or n.get("title") or "")[:50].strip()
                    s = (n.get("summary_zh") or "")[:80].strip()
                    if t and t not in seen:
                        seen.add(t)
                        lines.append(f"[{sym}] {t}")
                        if s: lines.append(f"  {s}")
                        lines.append("")
                    if len(seen) >= 5: break
                if len(seen) >= 5: break
            await push_text("\n".join(lines) if seen else "目前無最新新聞")
    except Exception as e:
        log.error(f"新聞失敗：{e}", exc_info=True)
        await push_text(f"新聞取得失敗：{type(e).__name__}")


async def process_sentiment_background():
    try:
        holdings = load_holdings()
        if not holdings:
            await push_text("尚無持股資料，請先上傳 CSV")
            return
        sentiment = await get_sentiment([h["symbol"] for h in holdings])
        await push_flex(build_sentiment_flex(sentiment), "社群情緒分析")
    except Exception as e:
        log.error(f"情緒失敗：{e}", exc_info=True)
        await push_text(f"社群情緒取得失敗：{type(e).__name__}")


async def process_screenshot_background(msg_id: str):
    try:
        img      = await download_file(msg_id)
        holdings = await extract_holdings_from_image(img, "image/jpeg")
        if not holdings:
            await push_text("辨識失敗，請確認截圖是嘉信持股頁面，文字清晰無遮擋")
            return
        save_holdings(holdings)
        await push_flex(
            build_success_flex(f"截圖辨識成功！{len(holdings)} 筆持股",
                               holdings, "按下方按鈕開始健檢"),
            "持股辨識完成"
        )
    except Exception as e:
        log.error(f"截圖辨識失敗：{e}", exc_info=True)
        await push_text(f"辨識失敗：{type(e).__name__}")


async def handle_csv_background(msg_id: str):
    try:
        data     = await download_file(msg_id)
        holdings = parse_schwab_csv_bytes(data)
        if not holdings:
            await push_text("CSV 解析失敗，請確認是嘉信持倉明細 CSV")
            return
        save_holdings(holdings, source="csv")
        await push_flex(
            build_success_flex(f"CSV 匯入成功！{len(holdings)} 筆持股",
                               holdings, "按下方按鈕開始健檢"),
            "持股更新完成"
        )
    except Exception as e:
        log.error(f"CSV 失敗：{e}", exc_info=True)
        await push_text(f"CSV 讀取失敗：{type(e).__name__}")


async def process_report_background():
    """舊版文字報告（保留備用）"""
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
        from notifier.line_push import push_report
        await push_report(await generate_report(holdings, t, s, n, f))
    except Exception as e:
        log.error(f"報告失敗：{e}", exc_info=True)
        await push_text(f"報告產生失敗：{type(e).__name__}")


# ── Webhook ───────────────────────────────────────

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

        user_id     = event["source"]["userId"]
        msg_type    = event["message"].get("type")
        reply_token = event["replyToken"]

        # CSV 檔案
        if msg_type == "file":
            filename = event["message"].get("fileName", "")
            if filename.lower().endswith(".csv"):
                await reply_text(reply_token, "讀取 CSV 中，完成後推播結果...")
                background_tasks.add_task(
                    handle_csv_background, event["message"]["id"])
            else:
                await reply_text(reply_token, "請上傳嘉信 CSV 持倉明細（.csv 格式）")
            continue

        # 截圖
        if msg_type == "image":
            await reply_text(reply_token, "辨識截圖中，完成後推播結果...")
            background_tasks.add_task(
                process_screenshot_background, event["message"]["id"])
            continue

        if msg_type != "text":
            continue

        text = event["message"]["text"].strip()

        if text in ("/help", "help", "使用說明", "說明", "?"):
            await reply_flex(reply_token, build_help_flex(), "使用說明")

        elif text in ("/overview", "/report", "今日總覽", "健檢", "報告"):
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
                await reply_text(reply_token,
                    "尚無持股資料，請上傳嘉信 CSV 檔案")
            else:
                # 先回覆條狀圖 Flex
                await reply_flex(reply_token,
                    build_holdings_pie_flex(holdings), "持股分布")
                # 背景推播真正的圓餅圖圖片
                import asyncio as _asyncio
                _asyncio.create_task(push_pie_chart(holdings))

        elif text in ("/sentiment", "社群情緒", "情緒"):
            await reply_text(reply_token, "蒐集社群情緒中，完成後推播...")
            background_tasks.add_task(process_sentiment_background)

        elif text in ("/status", "狀態", "資料狀態"):
            status = get_holdings_status()
            await reply_flex(reply_token,
                build_status_flex(status), "持股資料狀態")

        elif text in ("/reset", "重置", "清除記憶"):
            conversation_histories.pop(user_id, None)
            await reply_flex(reply_token, build_clear_flex(), "對話記憶已清除")

        elif text in ("略過",):
            await reply_text(reply_token, "好的！明天凌晨 5:30 再自動推播。")

        elif text in ("上傳CSV說明",):
            await reply_text(reply_token,
                "請到嘉信網頁版：\n"
                "Positions → 右上角 Export\n"
                "下載 CSV 後直接傳給我即可！")

        else:
            try:
                reply = await ask_claude(user_id, text)
            except Exception as e:
                reply = f"AI 回覆失敗：{type(e).__name__}"
            await reply_text(reply_token, reply)

    return {"status": "ok"}


@app.get("/chart/latest.png")
async def serve_chart():
    """提供最新圓餅圖圖片給 LINE Image Message 使用"""
    png = _chart_cache.get("latest", b"")
    if not png:
        holdings = load_holdings()
        if holdings:
            png = generate_pie_chart(holdings)
            _chart_cache["latest"] = png
    if png:
        return Response(content=png, media_type="image/png")
    return Response(status_code=404)


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
        return {"anthropic": "OK" if resp.status_code == 200
                else f"FAIL {resp.status_code}"}
    except Exception as e:
        return {"anthropic": f"FAIL: {e}"}
