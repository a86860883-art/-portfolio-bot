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
from analyzers.technical import analyze_technicals
from analyzers.ai_summary import generate_report
from sources.stocktwits import get_sentiment
from sources.news import get_news
from sources.sec_edgar import get_filings
from notifier.line_push import push_report, push_text, reply_text
from notifier.dashboard import send_dashboard

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

LINE_SECRET = os.environ["LINE_CHANNEL_SECRET"]
LINE_TOKEN  = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
GEMINI_KEY  = os.environ["GEMINI_API_KEY"]

conversation_histories: dict[str, list] = {}


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


async def download_image(msg_id: str) -> bytes:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"https://api-data.line.me/v2/bot/message/{msg_id}/content",
            headers={"Authorization": f"Bearer {LINE_TOKEN}"},
        )
        resp.raise_for_status()
        return resp.content


async def ask_claude(user_id: str, message: str) -> str:
    """使用 Gemini 回覆聊天問題"""
    api_key = os.environ["GEMINI_API_KEY"]
    history = conversation_histories.setdefault(user_id, [])
    history.append({"role": "user", "parts": [{"text": message}]})
    if len(history) > 20:
        history[:] = history[-20:]

    system = (
        "你是一個專業的美股持股健檢助理，專精於科技股分析。"
        "用繁體中文回答，簡潔易讀。只提供資訊分析，非投資建議。"
    )
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": history,
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 1024}
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()

    reply = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    history.append({"role": "model", "parts": [{"text": reply}]})
    return reply


# ── 背景任務：辨識截圖後用 push 推播結果 ──────────────
async def process_screenshot_background(msg_id: str):
    """在背景執行辨識，完成後用主動推播回傳結果，繞過 LINE 30 秒限制"""
    try:
        img = await download_image(msg_id)
        holdings = await extract_holdings_from_image(img, "image/jpeg")

        if not holdings:
            await push_text(
                "截圖辨識失敗，請確認：\n"
                "1. 截圖是嘉信持股頁面\n"
                "2. 文字清晰無遮擋\n"
                "3. 建議放大後再截圖\n\n"
                "支援 App 截圖與網頁版截圖"
            )
            return

        save_holdings(holdings)
        total = sum(h["market_value"] for h in holdings)
        lines = [f"辨識成功！更新了 {len(holdings)} 筆持股\n"]
        for h in sorted(holdings, key=lambda x: -x["market_value"]):
            sign = "▲" if h["unrealized_pl"] >= 0 else "▼"
            lines.append(
                f"{h['symbol']:<6} {h['quantity']:>6,.0f} 股"
                f"  ${h['market_value']:>8,.0f}"
                f"  {sign}${abs(h['unrealized_pl']):,.0f}"
            )
        lines += [f"\n總市值：${total:,.0f}", "傳 /report 可立即產生健檢報告"]
        await push_text("\n".join(lines))

    except Exception as e:
        log.error(f"背景辨識失敗：{e}")
        await push_text(f"辨識過程發生錯誤，請重新傳送截圖。\n（{type(e).__name__}）")


async def process_report_background():
    """在背景產生報告，完成後用 push 推播"""
    try:
        holdings = load_holdings()
        if not holdings:
            await push_text("尚無持股資料，請先傳送截圖")
            return
        tickers = [h["symbol"] for h in holdings]
        t, s, n, f = await asyncio.gather(
            analyze_technicals(tickers),
            get_sentiment(tickers),
            get_news(tickers),
            get_filings(tickers),
        )
        await push_report(await generate_report(holdings, t, s, n, f))
    except Exception as e:
        log.error(f"背景報告失敗：{e}")
        await push_text(f"報告產生失敗，請稍後重試。\n（{type(e).__name__}）")


HELP_TEXT = """📊 持股健檢 Bot 使用說明

【更新持股資料】
直接傳送截圖給我：
 - 嘉信 App 持股頁面截圖
 - 嘉信網頁版截圖
Bot 辨識完成後會自動推播結果

【指令】
/menu      互動儀表板
/holdings  持股清單與損益
/report    今日健檢報告
/technical 技術分析訊號
/sentiment 社群情緒（StockTwits）
/status    資料更新狀態
/reset     清除對話記憶
/help      顯示此說明

直接問問題也可以，例如：
「NVDA 目前技術面如何？」

每日凌晨 5:30 自動推播健檢報告"""


async def cmd_holdings() -> str:
    holdings = load_holdings()
    if not holdings:
        return "尚無持股資料\n請先傳送嘉信 App 或網頁版截圖"
    lines = ["持股清單\n" + "─" * 28]
    total = 0.0
    for h in sorted(holdings, key=lambda x: -x["market_value"]):
        sign = "▲" if h["unrealized_pl"] >= 0 else "▼"
        lines.append(
            f"{h['symbol']:<6} {h['quantity']:>7,.0f} 股"
            f"  ${h['market_value']:>9,.0f}"
            f"  {sign}${abs(h['unrealized_pl']):,.0f}"
        )
        total += h["market_value"]
    lines += ["─" * 28, f"總市值：${total:,.0f}"]
    return "\n".join(lines)


async def cmd_technical() -> str:
    holdings = load_holdings()
    if not holdings:
        return "尚無持股資料，請先傳送截圖"
    tech = await analyze_technicals([h["symbol"] for h in holdings])
    lines = ["技術分析訊號\n" + "─" * 24]
    for sym, t in tech.items():
        if "error" in t:
            lines.append(f"{sym}：分析失敗")
            continue
        sig = "、".join(t.get("signals", [])[:2]) or "無明顯訊號"
        above = "站上" if t["price"] > t["ma50"] else "跌破"
        lines.append(f"{sym}  ${t['price']}  RSI {t['rsi']}  {above} MA50\n  {sig}")
    return "\n".join(lines)


async def cmd_sentiment() -> str:
    holdings = load_holdings()
    if not holdings:
        return "尚無持股資料，請先傳送截圖"
    sent = await get_sentiment([h["symbol"] for h in holdings])
    lines = ["社群情緒（StockTwits）\n" + "─" * 24]
    for sym, s in sent.items():
        if not s.get("total"):
            lines.append(f"{sym}：無資料")
            continue
        mood = "偏多" if s["score"] > 20 else ("偏空" if s["score"] < -20 else "中性")
        lines.append(f"{sym}  {mood}  多{s['bullish']}/空{s['bearish']}  分數 {s['score']}")
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

        user_id     = event["source"]["userId"]
        msg_type    = event["message"].get("type")
        reply_token = event["replyToken"]

        # 截圖：立刻回覆「辨識中」，背景執行辨識後 push 結果
        if msg_type == "image":
            msg_id = event["message"]["id"]
            await reply_text(reply_token, "辨識截圖中，完成後會推播結果給你...")
            background_tasks.add_task(process_screenshot_background, msg_id)
            continue

        if msg_type != "text":
            continue

        text = event["message"]["text"].strip()

        if text in ("/help", "help", "說明", "?"):
            await reply_text(reply_token, HELP_TEXT)

        elif text in ("/menu", "menu", "儀表板", "功能表"):
            holdings = load_holdings()
            if not holdings:
                await reply_text(reply_token, "尚無持股資料，請先傳送截圖")
            else:
                await send_dashboard(reply_token, holdings)

        elif text in ("/holdings", "持股", "持股清單"):
            await reply_text(reply_token, await cmd_holdings())

        elif text in ("/report", "健檢", "報告"):
            await reply_text(reply_token, "報告產生中，完成後會推播給你（約 30 秒）...")
            background_tasks.add_task(process_report_background)

        elif text in ("/technical", "技術分析", "技術訊號"):
            await reply_text(reply_token, await cmd_technical())

        elif text in ("/sentiment", "情緒", "社群情緒"):
            await reply_text(reply_token, await cmd_sentiment())

        elif text in ("/status", "狀態", "資料狀態"):
            await reply_text(reply_token, get_holdings_status())

        elif text in ("/reset", "重置", "清除記憶"):
            conversation_histories.pop(user_id, None)
            await reply_text(reply_token, "對話記憶已清除！")

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


# ── 匯入並掛載測試路由 ──
from test_api import router as test_router
app.include_router(test_router)
