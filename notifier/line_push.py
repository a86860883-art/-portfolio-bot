"""
LINE 推播模組（截圖版）
所有環境變數在函數內讀取，確保 load_dotenv() 已執行
"""
import logging
import os
import httpx

log = logging.getLogger(__name__)

PUSH_URL  = "https://api.line.me/v2/bot/message/push"
REPLY_URL = "https://api.line.me/v2/bot/message/reply"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['LINE_CHANNEL_ACCESS_TOKEN']}",
        "Content-Type": "application/json",
    }


async def _push_messages(messages: list[dict]):
    user_id = os.environ["LINE_USER_ID"]
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            PUSH_URL, headers=_headers(),
            json={"to": user_id, "messages": messages}
        )
        resp.raise_for_status()


async def push_text(text: str):
    """主動推播單則文字"""
    await _push_messages([{"type": "text", "text": text[:4999]}])


async def push_report(report: str):
    """推播健檢報告，自動切分長文"""
    MAX = 4900
    chunks, text = [], report
    while len(text) > MAX:
        cut = text.rfind("\n", 0, MAX)
        cut = cut if cut > 0 else MAX
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        chunks.append(text)
    messages = [{"type": "text", "text": c} for c in chunks[:5]]
    await _push_messages(messages)
    log.info(f"報告推播完成（{len(messages)} 則）")


async def reply_text(reply_token: str, text: str):
    """回覆式推播（Webhook 用）"""
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(
            REPLY_URL, headers=_headers(),
            json={"replyToken": reply_token,
                  "messages": [{"type": "text", "text": text[:4999]}]}
        )


async def reply_flex(reply_token: str, flex: dict, alt: str = "持股儀表板"):
    """回覆 Flex Message（儀表板用）"""
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(
            REPLY_URL, headers=_headers(),
            json={"replyToken": reply_token,
                  "messages": [{"type": "flex", "altText": alt, "contents": flex}]}
        )
