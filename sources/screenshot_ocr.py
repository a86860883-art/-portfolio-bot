"""
截圖辨識模組 - Claude Vision 從嘉信 App / 網頁版截圖提取持股
"""
import base64
import json
import logging
import os
import re
from pathlib import Path
import httpx

log = logging.getLogger(__name__)

VISION_PROMPT = """
你是金融數據辨識專家。請分析這張嘉信證券（Charles Schwab）截圖，
可能來自手機 App 或網頁版，提取所有持股資訊。

以 JSON 格式回傳（只回傳 JSON，不要其他文字）：
{
  "holdings": [
    {
      "symbol": "股票代號（大寫，如 AAPL）",
      "quantity": 持股數量,
      "market_value": 市值（美元數字）,
      "cost_basis": 平均成本價（美元數字）,
      "unrealized_pl": 未實現損益（正數獲利負數虧損）,
      "unrealized_pl_pct": 損益百分比（數字）
    }
  ],
  "total_market_value": 總市值或null,
  "cash": 現金餘額或null,
  "confidence": "high/medium/low",
  "notes": "無法辨識的欄位說明"
}

規則：數字不含 $、,、% 符號。看不到的欄位設為 null。
股票代號一律大寫。若不是持股頁面，holdings 回傳空陣列。
"""


async def extract_holdings_from_image(image_data: bytes, media_type: str = "image/jpeg") -> list[dict]:
    """使用 Claude Vision 從截圖提取持股，回傳標準格式 list"""
    api_key = os.environ["ANTHROPIC_API_KEY"]
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
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1024,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64", "media_type": media_type, "data": b64}},
                        {"type": "text", "text": VISION_PROMPT},
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
        log.error(f"Vision JSON 解析失敗：{e}")
        return []

    raw_holdings = result.get("holdings", [])
    confidence   = result.get("confidence", "unknown")
    notes        = result.get("notes", "")
    if notes:
        log.info(f"Vision 備註：{notes}")
    log.info(f"辨識完成，信心度：{confidence}，持股：{len(raw_holdings)} 筆")

    def sf(v, d=0.0):
        try: return float(v) if v is not None else d
        except: return d

    return [
        {
            "symbol":        h.get("symbol", "").strip().upper(),
            "quantity":      sf(h.get("quantity")),
            "market_value":  sf(h.get("market_value")),
            "cost_basis":    sf(h.get("cost_basis")),
            "unrealized_pl": sf(h.get("unrealized_pl")),
            "source":        "screenshot",
            "confidence":    confidence,
        }
        for h in raw_holdings if h.get("symbol")
    ]


async def extract_from_file(path: str) -> list[dict]:
    """從本機圖片檔案提取持股（測試用）"""
    p = Path(path)
    ext = p.suffix.lower()
    media_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".png": "image/png", ".webp": "image/webp"}
    return await extract_holdings_from_image(
        p.read_bytes(), media_map.get(ext, "image/jpeg"))
