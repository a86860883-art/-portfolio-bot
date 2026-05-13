"""
FastAPI 主伺服器
- LINE Webhook 接收聊天訊息（問持股狀況）
- 背景執行交易監控輪詢
- 健康檢查端點
"""
import asyncio
import hashlib
import hmac
import base64
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import httpx

from monitor.trade_monitor import poll_once, run_monitor
from sources.schwab import get_holdings
from analyzers.technical import analyze_technicals
from analyzers.ai_summary import generate_report
from sources.stocktwits import get_sentiment
from sources.news import get_news
from sources.sec_edgar import get_filings
from notifier.line_push import push_report

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

LINE_CHANNEL_SECRET       = os.environ["LINE_CHANNEL_SECRET"]
LINE_CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
ANTHROPIC_API_KEY         = os.environ["ANTHROPIC_API_KEY"]

# 每位用戶對話歷史
conversation_histories: dict[str, list] = {}


# ── 背景任務 ──────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """伺服器啟動時，同步啟動交易監控背景迴圈"""
    task = asyncio.create_task(run_monitor())
    log.info("交易監控背景任務已啟動")
    yield
    task.cancel()
    log.info("交易監控背景任務已停止")

app = FastAPI(title="持股健檢 Bot", lifespan=lifespan)


# ── 工具函式 ──────────────────────────────────────────────
def verify_signature(body: bytes, signature: str) -> bool:
    digest = hmac.new(
        LINE_CHANNEL_SECRET.encode(), body, hashlib.sha256
    ).digest()
    return hmac.compare_digest(
        base64.b64encode(digest).decode(), signature
    )


async def reply_line(reply_token: str, text: str):
    # LINE 單則上限 5000 字
    text = text[:4999]
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(
            "https://api.line.me/v2/bot/message/reply",
            headers={
                "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            },
            json={"replyToken": reply_token,
                  "messages": [{"type": "text", "text": text}]},
        )


async def ask_claude_chat(user_id: str, user_message: str) -> str:
    """一般聊天問答（帶對話記憶）"""
    history = conversation_histories.setdefault(user_id, [])
    history.append({"role": "user", "content": user_message})
    if len(history) > 20:
        history[:] = history[-20:]

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1024,
                "system": (
                    "你是一個專業的股票持股健檢助理，使用者的持股在嘉信證券。"
                    "請用繁體中文回答，回覆簡潔易讀。"
                    "若使用者問及具體投資建議，提醒你只提供資訊分析，非投資建議。"
                ),
                "messages": history,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    reply = data["content"][0]["text"]
    history.append({"role": "assistant", "content": reply})
    return reply


# ── 指令處理 ──────────────────────────────────────────────
HELP_TEXT = """📊 持股健檢 Bot 指令列表

/report    立即產生今日健檢報告
/holdings  查看目前持股清單
/check     手動觸發交易監控
/reset     清除對話記憶
/help      顯示此說明

其他訊息可直接輸入問題，例如：
「NVDA 最近技術面如何？」
「今天市場整體狀況怎樣？」"""


async def cmd_holdings() -> str:
    holdings = await get_holdings()
    if not holdings:
        return "⚠️ 無法取得持股資料，請確認嘉信 Token 是否有效。"
    lines = ["📋 目前持股清單\n" + "─" * 20]
    total = 0.0
    for h in holdings:
        pl_sign = "▲" if h["unrealized_pl"] >= 0 else "▼"
        lines.append(
            f"{h['symbol']:<6} {h['quantity']:>6,.0f} 股"
            f"  市值 ${h['market_value']:>10,.0f}"
            f"  {pl_sign} ${abs(h['unrealized_pl']):,.0f}"
        )
        total += h["market_value"]
    lines.append("─" * 20)
    lines.append(f"總市值：${total:,.0f}")
    return "\n".join(lines)


async def cmd_report() -> str:
    holdings = await get_holdings()
    if not holdings:
        return "⚠️ 無法取得持股，無法產生報告。"
    tickers = [h["symbol"] for h in holdings]
    technicals, sentiment, news, filings = await asyncio.gather(
        analyze_technicals(tickers),
        get_sentiment(tickers),
        get_news(tickers),
        get_filings(tickers),
    )
    report = await generate_report(holdings, technicals, sentiment, news, filings)
    return report


async def cmd_check() -> str:
    new = await poll_once()
    if new:
        return f"✅ 偵測到 {new} 筆新成交，已推播通知。"
    return "✅ 交易監控執行完成，無新成交紀錄。"


# ── Webhook 端點 ──────────────────────────────────────────
@app.post("/webhook")
async def webhook(request: Request):
    body = await request.body()
    sig  = request.headers.get("X-Line-Signature", "")

    if not verify_signature(body, sig):
        raise HTTPException(status_code=400, detail="Invalid signature")

    payload = json.loads(body)

    for event in payload.get("events", []):
        if event.get("type") != "message":
            continue
        if event["message"].get("type") != "text":
            continue

        user_id     = event["source"]["userId"]
        text        = event["message"]["text"].strip()
        reply_token = event["replyToken"]

        # 指令路由
        if text in ("/help", "help", "說明"):
            await reply_line(reply_token, HELP_TEXT)

        elif text in ("/reset", "重置", "清除記憶"):
            conversation_histories.pop(user_id, None)
            await reply_line(reply_token, "✅ 對話記憶已清除！")

        elif text == "/holdings":
            await reply_line(reply_token, "⏳ 查詢持股中，請稍候...")
            result = await cmd_holdings()
            await reply_line(reply_token, result)

        elif text == "/report":
            await reply_line(reply_token, "⏳ 產生健檢報告中，約需 30 秒...")
            report = await cmd_report()
            # 報告可能很長，用 push 發送（reply 只能用一次）
            await push_report(report)

        elif text == "/check":
            await reply_line(reply_token, "⏳ 執行交易監控中...")
            result = await cmd_check()
            await reply_line(reply_token, result)

        else:
            # 一般 AI 問答
            try:
                reply = await ask_claude_chat(user_id, text)
            except Exception as e:
                reply = f"⚠️ AI 回覆失敗：{type(e).__name__}"
            await reply_line(reply_token, reply)

    return {"status": "ok"}


# ── 健康檢查 ──────────────────────────────────────────────
@app.get("/")
async def health():
    return {
        "status": "running",
        "time":   datetime.now().isoformat(),
        "bot":    "持股健檢 Bot",
    }
