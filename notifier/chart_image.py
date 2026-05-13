"""
圖表圖片產生模組
matplotlib 產生圓餅圖 → LINE Image Send API
"""
import base64
import io
import logging
import os
import httpx

log = logging.getLogger(__name__)

COLORS = ["#534AB7","#1D9E75","#E24B4A","#185FA5","#BA7517",
          "#D4537E","#0F6E56","#888780","#C07000","#3C3489"]

PUSH_URL  = "https://api.line.me/v2/bot/message/push"
REPLY_URL = "https://api.line.me/v2/bot/message/reply"

# CJK 字型路徑
CJK_FONT_PATH = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    if os.path.exists("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    else "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"
    if os.path.exists("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc")
    else None
)


def _setup_font():
    """設定中文字型"""
    if not CJK_FONT_PATH:
        return None
    try:
        import matplotlib.font_manager as fm
        fm.fontManager.addfont(CJK_FONT_PATH)
        prop = fm.FontProperties(fname=CJK_FONT_PATH)
        return prop.get_name()
    except Exception:
        return None


def generate_pie_chart(holdings: list) -> bytes:
    """產生持股圓餅圖，回傳 PNG bytes"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    font_name = _setup_font()
    if font_name:
        plt.rcParams["font.family"] = font_name

    total = sum(h["market_value"] for h in holdings)
    if total == 0:
        return b""

    sorted_h = sorted(holdings, key=lambda x: -x["market_value"])
    top8     = sorted_h[:8]
    others   = sorted_h[8:]

    labels = [h["symbol"] for h in top8]
    sizes  = [h["market_value"] for h in top8]
    colors = COLORS[:len(top8)]
    if others:
        labels.append("其他")
        sizes.append(sum(h["market_value"] for h in others))
        colors.append("#CCCCCC")

    explode = [0.05 if s == max(sizes) else 0 for s in sizes]

    fig, ax = plt.subplots(figsize=(8, 6), facecolor="#1C1C1E")
    ax.set_facecolor("#1C1C1E")

    wedges, _, autotexts = ax.pie(
        sizes, labels=None, colors=colors, explode=explode,
        autopct=lambda p: f"{p:.1f}%" if p >= 4 else "",
        startangle=90, pctdistance=0.75,
        wedgeprops={"linewidth": 2, "edgecolor": "#1C1C1E"},
    )
    for at in autotexts:
        at.set_color("white")
        at.set_fontsize(11)
        at.set_fontweight("bold")

    total_pl = sum(h["unrealized_pl"] for h in holdings)
    sign     = "+" if total_pl >= 0 else ""
    pl_color = "#5DCAA5" if total_pl >= 0 else "#FF6B6B"
    ax.text(0, 0.1, f"${total/1000:.1f}K",
            ha="center", va="center",
            fontsize=16, color="white", fontweight="bold")
    ax.text(0, -0.15, f"{sign}${abs(total_pl)/1000:.1f}K",
            ha="center", va="center",
            fontsize=12, color=pl_color, fontweight="bold")

    ax.legend(
        wedges,
        [f"{l}  {s/total*100:.1f}%" for l, s in zip(labels, sizes)],
        loc="center left", bbox_to_anchor=(0.9, 0.5),
        fontsize=10, framealpha=0, labelcolor="white",
    )
    ax.set_title("持股分布", color="white", fontsize=14,
                 fontweight="bold", pad=12)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150,
                bbox_inches="tight", facecolor="#1C1C1E")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


async def _send_image_message(png_bytes: bytes,
                               token_or_reply: str,
                               is_reply: bool = False):
    """
    LINE 不支援 data URI，需要用 LINE 的 Image Send API
    這裡改用 base64 encoded image 上傳到臨時服務，
    或直接用 LINE Bot SDK 的 SendMessage with imageUrl。

    最簡單可靠的方式：把圖片 encode 進 Flex Message 的 hero image
    LINE Flex hero image 支援 https URL，但不支援 base64。

    → 改用「上傳到 Railway 自身的靜態端點」提供 URL
    → 或改用「把圖片直接用 /upload 端點傳給 LINE」
    
    實際上最可靠的方案：存成暫存檔案，透過 /image endpoint 提供
    """
    token   = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
    user_id = os.environ.get("LINE_USER_ID", "")
    
    url = REPLY_URL if is_reply else PUSH_URL
    to  = token_or_reply if is_reply else user_id

    key = "replyToken" if is_reply else "to"

    # 儲存到記憶體供 /chart endpoint 提供
    _chart_cache["latest"] = png_bytes

    # 取得 Railway 的公開網址
    base_url = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    if base_url and not base_url.startswith("http"):
        base_url = f"https://{base_url}"

    if not base_url:
        log.warning("無法取得 RAILWAY_PUBLIC_DOMAIN，圓餅圖無法以圖片發送")
        return False

    img_url = f"{base_url}/chart/latest.png"

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            json={
                key: to,
                "messages": [{
                    "type": "image",
                    "originalContentUrl": img_url,
                    "previewImageUrl": img_url,
                }]
            }
        )
    if resp.status_code == 200:
        log.info("圓餅圖圖片推播成功")
        return True
    else:
        log.warning(f"圓餅圖推播失敗：{resp.status_code} {resp.text[:100]}")
        return False


# 記憶體快取，供 /chart endpoint 使用
_chart_cache: dict = {}


async def push_pie_chart(holdings: list) -> bool:
    """產生並推播圓餅圖"""
    try:
        png = generate_pie_chart(holdings)
        if not png:
            return False
        return await _send_image_message(png, "", is_reply=False)
    except Exception as e:
        log.warning(f"push_pie_chart 失敗：{e}")
        return False


async def reply_pie_chart(reply_token: str, holdings: list) -> bool:
    """產生並回覆圓餅圖"""
    try:
        png = generate_pie_chart(holdings)
        if not png:
            return False
        return await _send_image_message(png, reply_token, is_reply=True)
    except Exception as e:
        log.warning(f"reply_pie_chart 失敗：{e}")
        return False
