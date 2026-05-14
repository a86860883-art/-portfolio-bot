"""
帳戶總結截圖辨識模組
辨識嘉信 App 的帳戶餘額頁面
"""
import base64
import io
import json
import logging
import os
import re
import httpx

log = logging.getLogger(__name__)

BALANCE_PROMPT = """
請分析這張嘉信證券帳戶截圖，可能來自嘉信主 App、Thinkorswim App 或網頁版，提取帳戶資訊。

只回傳 JSON，不要其他文字：
{
  "net_value": 帳戶淨值（數字，美元）,
  "margin_balance": 融資借款金額（正數，美元，若無融資則為0）,
  "total_market_value": 持股總市值（數字，美元）,
  "available_cash": 可用資金（數字，美元）,
  "margin_equity_pct": 淨資產百分比（數字，如72.00）,
  "maintenance_requirement": 維持保證金要求（數字，美元）,
  "confidence": "high/medium/low",
  "notes": "備註"
}

各 App 欄位對應說明：

【嘉信主 App / 帳戶總結】
- 融資淨資產 / 淨清倉價值 → net_value
- 融資餘額（括號表示負數）→ margin_balance（取絕對值）
- 買入股票價值 / 買入可融資價值 → total_market_value
- 可用於交易的資金 → available_cash
- 淨資產百分比 → margin_equity_pct
- 維持保證金要求 → maintenance_requirement

【Thinkorswim App】
- 淨清倉值 / 倉位淨資產 / 淨清倉價值 → net_value
- 若有負數現金或融資欄位 → margin_balance
- 開倉盈虧 + 淨清倉值可推算市值 → total_market_value
- 可用$ → available_cash

【計算說明】
- 括號金額（$57,041.47）代表負數，融資借款取絕對值
- 若截圖只有淨清倉值和可用資金，margin_balance 設為 0
- total_market_value 若看不到，用 net_value + margin_balance 估算

數字不含 $、,、% 符號。看不到的欄位設為 null。
"""

MAX_BYTES = 4 * 1024 * 1024


def _compress(data: bytes) -> bytes:
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        if len(data) <= MAX_BYTES:
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=85)
            return out.getvalue()
        for q in [70, 55, 40]:
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=q)
            if len(out.getvalue()) <= MAX_BYTES:
                return out.getvalue()
    except ImportError:
        pass
    return data


async def extract_balance_from_image(image_data: bytes) -> dict:
    """使用 Claude Vision 辨識帳戶總結截圖"""
    api_key = os.environ["ANTHROPIC_API_KEY"]
    image_data = _compress(image_data)
    b64 = base64.standard_b64encode(image_data).decode()

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 512,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": b64,
                        }},
                        {"type": "text", "text": BALANCE_PROMPT},
                    ],
                }],
            },
        )
        resp.raise_for_status()

    raw = resp.json()["content"][0]["text"].strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as e:
        log.error(f"Balance OCR JSON 解析失敗：{e}")
        return {}

    def sf(v):
        try: return float(v) if v is not None else 0.0
        except: return 0.0

    balance = {
        "net_value":               sf(result.get("net_value")),
        "margin_balance":          sf(result.get("margin_balance")),
        "total_market_value":      sf(result.get("total_market_value")),
        "available_cash":          sf(result.get("available_cash")),
        "margin_equity_pct":       sf(result.get("margin_equity_pct")),
        "maintenance_requirement": sf(result.get("maintenance_requirement")),
        "confidence":              result.get("confidence", "unknown"),
        "notes":                   result.get("notes", ""),
    }

    log.info(
        f"帳戶辨識完成：淨值 ${balance['net_value']:,.0f}，"
        f"融資 ${balance['margin_balance']:,.0f}，"
        f"信心度 {balance['confidence']}"
    )
    return balance
