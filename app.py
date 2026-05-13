from dotenv import load_dotenv
load_dotenv()

import asyncio, hashlib, hmac, base64, json, logging, os
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request, HTTPException
import httpx

from scheduler.daily import create_scheduler
from sources.screenshot_ocr import extract_holdings_from_image
from sources.holdings_store import save_holdings, load_holdings, get_holdings_status
from analyzers.technical import analyze_technicals
from analyzers.ai_summary import generate_report
from sources.stocktwits import get_sentiment
from sources.news import get_news
from sources.sec_edgar import get_filings
from notifier.line_push import push_report, reply_text, reply_flex
from notifier.dashboard import send_dashboard

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

LINE_SECRET = os.environ["LINE_CHANNEL_SECRET"]
LINE_TOKEN  = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
ANTH_KEY    = os.environ["ANTHROPIC_API_KEY"]

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
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1024,
                "system": (
                    "你是一個專業的美股持股健檢助理，專精於 TSLA、NVDA、GOOGL、MU、INTC 等科技股。"
                    "用繁體中文回答，簡潔易讀。只提供資訊分析，非投資建議。"
                    "回答時可主動提示該股的技術分析盲點。"
                ),
                "messages": history,
            },
        )
        resp.raise_for_status()
    reply = resp.json()["content"][0]["text"]
    history.append({"role": "assistant", "content": reply})
    return reply


HELP_TEXT = """📊 持股健檢 Bot 使用說明

【更新持股資料】
直接傳送截圖給我：
 - 嘉信 App 持股頁面截圖
 - 嘉信網頁版截圖
Bot 會自動辨識並更新持股資料

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


async def handle_screenshot(reply_token: str, msg_id: str):
    await reply_text(reply_token, "辨識截圖中，請稍候...")
    try:
        img = await download_image(msg_id)
    except Exception as e:
        await reply_text(reply_token, f"圖片下載失敗：{e}\n請重新傳送")
        return

    holdings = await extract_holdings_from_image(img, "image/jpeg")
    if not holdings:
        await reply_text(
            reply_token,
            "辨識失敗，請確認：\n"
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
    await reply_text(reply_token, "\n".join(lines))


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
            lines.append(f"{sym}：分析失敗（{t['error']}）")
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
async def webhook(request: Request):
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

        if msg_type == "image":
            await handle_screenshot(reply_token, event["message"]["id"])
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
            await reply_text(reply_token, "產生報告中，約需 30 秒...")
            holdings = load_holdings()
            if holdings:
                tickers = [h["symbol"] for h in holdings]
                t, s, n, f = await asyncio.gather(
                    analyze_technicals(tickers),
                    get_sentiment(tickers),
                    get_news(tickers),
                    get_filings(tickers),
                )
                await push_report(await generate_report(holdings, t, s, n, f))
            else:
                await reply_text(reply_token, "尚無持股資料，請先傳送截圖")

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
