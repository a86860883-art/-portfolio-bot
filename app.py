from dotenv import load_dotenv
load_dotenv()

import asyncio, hashlib, hmac, base64, json, logging, os
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import Response
import httpx

from scheduler.daily import create_scheduler
from sources.screenshot_ocr import extract_holdings_from_image
from sources.balance_ocr import extract_balance_from_image
from sources.balance_store import save_balance, load_balance, calc_leverage
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
    build_balance_success_flex,
    build_status_flex, build_help_flex, build_clear_flex,
    push_flex, reply_flex
)
from notifier.chart_image import (
    generate_pie_chart, push_pie_chart, _chart_cache
)

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")

LINE_SECRET = os.environ["LINE_CHANNEL_SECRET"]
LINE_TOKEN  = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
ANTH_KEY    = os.environ["ANTHROPIC_API_KEY"]

conversation_histories: dict[str, list] = {}
_last_technicals: dict = {}

# 辨識模式旗標：None=一般截圖, "balance"=帳戶截圖
_pending_image_mode: dict[str, str] = {}


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
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 1024,
                  "system": ("你是專業美股持股健檢助理，專精科技股分析。"
                             "用繁體中文回答，簡潔易讀，非投資建議。"),
                  "messages": history},
        )
        resp.raise_for_status()
    reply = resp.json()["content"][0]["text"]
    history.append({"role": "assistant", "content": reply})
    return reply


# ── 背景任務 ──────────────────────────────────────────

async def process_overview_background():
    global _last_technicals
    try:
        holdings = load_holdings()
        if not holdings:
            await push_text("尚無持股資料，請先上傳嘉信 CSV 檔案")
            return
        technicals = await analyze_technicals([h["symbol"] for h in holdings])
        _last_technicals = technicals
        balance = load_balance()
        today   = datetime.now().strftime("%Y/%m/%d")
        await push_flex(
            build_overview_flex(holdings, technicals, today, balance),
            "持股健檢總覽")
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
            market = news.get("market", []) if isinstance(news, dict) else []
            stocks = news.get("stocks", news) if isinstance(news, dict) else news
            lines  = ["🗞 重大市場新聞\n"]
            for n in market[:3]:
                t = (n.get("title_zh") or n.get("title") or "")[:45]
                s = (n.get("summary_zh") or "")[:70]
                if t: lines.append(f"• {t}\n  {s}\n")
            lines.append("\n📌 持股相關新聞\n")
            seen = set()
            for n in stocks[:5]:
                t = (n.get("title_zh") or n.get("title") or "")[:45]
                if t and t not in seen:
                    seen.add(t)
                    lines.append(f"• [{n.get('symbol','')}] {t}\n")
            await push_text("\n".join(lines))
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
    """持股截圖辨識"""
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
            "持股辨識完成")
    except Exception as e:
        log.error(f"截圖辨識失敗：{e}", exc_info=True)
        await push_text(f"辨識失敗：{type(e).__name__}")


async def process_auto_detect_screenshot(msg_id: str):
    """
    自動判斷截圖類型：
    - 有淨清倉值/融資欄位 → 帳戶總結截圖
    - 有股票代號/持股列表 → 持股截圖
    """
    try:
        img = await download_file(msg_id)

        # 先試帳戶辨識
        balance = await extract_balance_from_image(img)
        if balance and balance.get("net_value", 0) > 0:
            # 成功辨識為帳戶截圖
            save_balance(balance)
            await push_flex(build_balance_success_flex(balance), "帳戶資訊已更新")
            return

        # 再試持股辨識
        holdings = await extract_holdings_from_image(img, "image/jpeg")
        if holdings:
            save_holdings(holdings)
            await push_flex(
                build_success_flex(f"截圖辨識成功！{len(holdings)} 筆持股",
                                   holdings, "按下方按鈕開始健檢"),
                "持股辨識完成")
            return

        # 兩者都失敗
        await push_text(
            "截圖辨識失敗，請確認截圖內容：\n\n"
            "📋 持股清單截圖 → 直接傳送\n"
            "🏦 帳戶總結截圖 → 點選單「更新帳戶」再傳\n\n"
            "建議截圖時放大頁面，確保文字清晰"
        )
    except Exception as e:
        log.error(f"自動辨識失敗：{e}", exc_info=True)
        await push_text(f"辨識失敗：{type(e).__name__}")


async def process_balance_screenshot_background(msg_id: str):
    """帳戶總結截圖辨識"""
    try:
        img     = await download_file(msg_id)
        balance = await extract_balance_from_image(img)
        if not balance or balance.get("net_value", 0) == 0:
            await push_text(
                "帳戶資訊辨識失敗，請確認截圖是嘉信「帳戶總結」或「餘額」頁面\n"
                "需包含：淨清倉價值、融資餘額等欄位"
            )
            return
        save_balance(balance)
        await push_flex(build_balance_success_flex(balance), "帳戶資訊已更新")
    except Exception as e:
        log.error(f"帳戶截圖辨識失敗：{e}", exc_info=True)
        await push_text(f"帳戶資訊辨識失敗：{type(e).__name__}")


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
            "持股更新完成")
    except Exception as e:
        log.error(f"CSV 失敗：{e}", exc_info=True)
        await push_text(f"CSV 讀取失敗：{type(e).__name__}")


# ── Webhook ───────────────────────────────────────────

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

        # 圖片 - 自動判斷是持股截圖還是帳戶截圖
        if msg_type == "image":
            mode   = _pending_image_mode.pop(user_id, None)
            msg_id = event["message"]["id"]
            if mode == "balance":
                await reply_text(reply_token, "辨識帳戶資訊中，完成後推播結果...")
                background_tasks.add_task(
                    process_balance_screenshot_background, msg_id)
            else:
                # 自動判斷：先試帳戶截圖辨識，若失敗改持股辨識
                await reply_text(reply_token, "辨識截圖中，完成後推播結果...")
                background_tasks.add_task(
                    process_auto_detect_screenshot, msg_id)
            continue

        if msg_type != "text":
            continue

        text = event["message"]["text"].strip()

        if text in ("/help", "help", "使用說明", "說明", "?"):
            await reply_flex(reply_token, build_help_flex(), "使用說明")

        elif text in ("/balance", "帳戶資訊", "更新帳戶"):
            _pending_image_mode[user_id] = "balance"
            await reply_text(reply_token,
                "請傳送嘉信 App「帳戶總結」頁面的截圖\n"
                "需包含：淨清倉價值、融資餘額、可用資金等欄位")

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
                await reply_text(reply_token, "尚無持股資料，請上傳嘉信 CSV 檔案")
            else:
                balance = load_balance()
                await reply_flex(reply_token,
                    build_holdings_pie_flex(holdings, balance), "持股分布")
                asyncio.create_task(push_pie_chart(holdings))

        elif text in ("/sentiment", "社群情緒", "情緒"):
            await reply_text(reply_token, "蒐集社群情緒中，完成後推播...")
            background_tasks.add_task(process_sentiment_background)

        elif text in ("/status", "狀態", "資料狀態"):
            await reply_flex(reply_token,
                build_status_flex(get_holdings_status()), "持股資料狀態")

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
                json={"model": "claude-haiku-4-5-20251001", "max_tokens": 10,
                      "messages": [{"role": "user", "content": "hi"}]},
            )
        return {"anthropic": "OK" if resp.status_code == 200
                else f"FAIL {resp.status_code}"}
    except Exception as e:
        return {"anthropic": f"FAIL: {e}"}
