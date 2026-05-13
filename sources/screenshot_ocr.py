"""
截圖辨識模組 - 使用 Claude Vision (Anthropic)
"""
import base64, io, json, logging, os, re
from pathlib import Path
import httpx

log = logging.getLogger(__name__)

VISION_PROMPT = """
你是金融數據辨識專家。請分析這張嘉信證券截圖，提取所有持股資訊。
只回傳 JSON，不要其他文字：
{
  "holdings": [
    {
      "symbol": "股票代號（大寫）",
      "quantity": 持股數量,
      "market_value": 市值,
      "cost_basis": 平均成本價,
      "unrealized_pl": 未實現損益,
      "unrealized_pl_pct": 損益百分比
    }
  ],
  "confidence": "high/medium/low",
  "notes": "備註"
}
數字不含$、,、%。看不到設null。非持股頁面holdings回傳空陣列。
"""

MAX_BYTES = 4 * 1024 * 1024


def _compress(data: bytes) -> tuple[bytes, str]:
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        for q in [85, 70, 55, 40]:
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=q)
            c = out.getvalue()
            if len(c) <= MAX_BYTES:
                return c, "image/jpeg"
        w, h = img.size
        img = img.resize((w//2, h//2))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=60)
        return out.getvalue(), "image/jpeg"
    except ImportError:
        return data, "image/jpeg"


async def extract_holdings_from_image(image_data: bytes, media_type: str = "image/jpeg") -> list[dict]:
    api_key = os.environ["ANTHROPIC_API_KEY"]
    image_data, media_type = _compress(image_data)
    b64 = base64.standard_b64encode(image_data).decode()
    log.info(f"圖片：{len(image_data)//1024}KB")

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
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
        if resp.status_code != 200:
            log.error(f"Anthropic 錯誤 {resp.status_code}：{resp.text[:300]}")
        resp.raise_for_status()

    raw = resp.json()["content"][0]["text"].strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as e:
        log.error(f"JSON 解析失敗：{e}")
        return []

    raw_h = result.get("holdings", [])
    log.info(f"辨識完成：{result.get('confidence')}，{len(raw_h)} 筆")

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
            "confidence":    result.get("confidence", "unknown"),
        }
        for h in raw_h if h.get("symbol")
    ]
