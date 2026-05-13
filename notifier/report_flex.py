"""
兩階段報告 Flex Message 產生器
第一階段：總覽燈號卡片
第二階段：個股詳細分析卡片群
"""
import os
import logging
import httpx

log = logging.getLogger(__name__)

PUSH_URL  = "https://api.line.me/v2/bot/message/push"
REPLY_URL = "https://api.line.me/v2/bot/message/reply"


def _headers():
    return {
        "Authorization": f"Bearer {os.environ['LINE_CHANNEL_ACCESS_TOKEN']}",
        "Content-Type": "application/json",
    }


def _signal(t: dict) -> tuple[str, str, str]:
    """
    根據技術指標回傳 (燈號顏色, 評級文字, 評級背景色)
    """
    if not t or "error" in t:
        return "#888780", "無資料", "#F1EFE8"

    rsi    = t.get("rsi", 50)
    bb_pct = t.get("bb_pct", 50)
    price  = t.get("price", 0)
    ma50   = t.get("ma50", 0)
    pct_h  = t.get("pct_from_high", 0)

    score = 0
    if price and ma50 and price > ma50: score += 1
    if rsi < 70: score += 1
    if bb_pct < 80: score += 1
    if pct_h > -20: score += 1

    if score >= 3:
        return "#1D9E75", "謹慎樂觀", "#E1F5EE"
    elif score == 2:
        return "#BA7517", "中性觀察", "#FAEEDA"
    else:
        return "#E24B4A", "注意風險", "#FCEBEB"


def build_overview_flex(holdings: list, technicals: dict, date_str: str) -> dict:
    """第一階段：總覽燈號卡片"""
    total_mv = sum(h["market_value"] for h in holdings)
    total_pl = sum(h["unrealized_pl"] for h in holdings)
    total_day = sum(h.get("day_change", 0) for h in holdings)
    pl_color  = "#1D9E75" if total_pl >= 0 else "#E24B4A"
    day_color = "#1D9E75" if total_day >= 0 else "#E24B4A"
    pl_sign   = "+" if total_pl >= 0 else ""
    day_sign  = "+" if total_day >= 0 else ""

    red_count    = sum(1 for h in holdings if _signal(technicals.get(h["symbol"], {}))[0] == "#E24B4A")
    yellow_count = sum(1 for h in holdings if _signal(technicals.get(h["symbol"], {}))[0] == "#BA7517")

    rows = []
    for h in sorted(holdings, key=lambda x: -x["market_value"]):
        sym = h["symbol"]
        t   = technicals.get(sym, {})
        color, label, _ = _signal(t)
        rsi = t.get("rsi", "--")
        rows.append({
            "type": "box",
            "layout": "horizontal",
            "contents": [
                {"type": "box", "layout": "vertical",
                 "width": "8px", "backgroundColor": color,
                 "contents": [{"type": "filler"}]},
                {"type": "box", "layout": "horizontal",
                 "paddingAll": "8px", "flex": 1,
                 "contents": [
                     {"type": "text", "text": sym, "weight": "bold",
                      "size": "sm", "color": "#111111", "flex": 2},
                     {"type": "text", "text": label,
                      "size": "xs", "color": color, "flex": 3},
                     {"type": "text", "text": f"RSI {rsi}",
                      "size": "xs", "color": "#888888",
                      "align": "end", "flex": 2},
                 ]},
            ],
            "backgroundColor": "#FFFFFF",
            "borderWidth": "0.5px",
            "borderColor": "#F0F0F0",
            "margin": "xs",
        })

    return {
        "type": "bubble", "size": "giga",
        "header": {
            "type": "box", "layout": "vertical",
            "backgroundColor": "#111111", "paddingAll": "16px",
            "contents": [
                {"type": "box", "layout": "horizontal", "contents": [
                    {"type": "text", "text": "持股健檢總覽",
                     "weight": "bold", "size": "lg", "color": "#FFFFFF", "flex": 1},
                    {"type": "text", "text": date_str,
                     "size": "xs", "color": "#888888", "align": "end"},
                ]},
                {"type": "box", "layout": "horizontal",
                 "margin": "sm", "contents": [
                     {"type": "text",
                      "text": f"🔴 {red_count} 注意  🟡 {yellow_count} 觀察  ⚫ {len(holdings)-red_count-yellow_count} 正常",
                      "size": "xs", "color": "#AAAAAA"},
                ]},
            ]
        },
        "body": {
            "type": "box", "layout": "vertical",
            "paddingAll": "12px", "spacing": "sm",
            "contents": [
                {"type": "box", "layout": "horizontal",
                 "spacing": "sm", "contents": [
                     {"type": "box", "layout": "vertical", "flex": 1,
                      "backgroundColor": "#F7F7F7", "cornerRadius": "8px",
                      "paddingAll": "10px", "contents": [
                          {"type": "text", "text": "總市值", "size": "xs", "color": "#888888"},
                          {"type": "text", "text": f"${total_mv:,.0f}",
                           "size": "md", "weight": "bold", "color": "#111111"},
                      ]},
                     {"type": "box", "layout": "vertical", "flex": 1,
                      "backgroundColor": "#F7F7F7", "cornerRadius": "8px",
                      "paddingAll": "10px", "contents": [
                          {"type": "text", "text": "未實現損益", "size": "xs", "color": "#888888"},
                          {"type": "text",
                           "text": f"{pl_sign}${abs(total_pl):,.0f}",
                           "size": "md", "weight": "bold", "color": pl_color},
                      ]},
                     {"type": "box", "layout": "vertical", "flex": 1,
                      "backgroundColor": "#F7F7F7", "cornerRadius": "8px",
                      "paddingAll": "10px", "contents": [
                          {"type": "text", "text": "今日損益", "size": "xs", "color": "#888888"},
                          {"type": "text",
                           "text": f"{day_sign}${abs(total_day):,.0f}",
                           "size": "md", "weight": "bold", "color": day_color},
                      ]},
                ]},
                {"type": "separator"},
                {"type": "text", "text": "持股燈號",
                 "size": "xs", "color": "#888888", "weight": "bold"},
                *rows,
            ]
        },
        "footer": {
            "type": "box", "layout": "horizontal",
            "paddingAll": "10px", "spacing": "sm",
            "contents": [
                {"type": "button",
                 "action": {"type": "message",
                            "label": "略過，明天再看",
                            "text": "略過"},
                 "style": "secondary", "height": "sm", "flex": 1},
                {"type": "button",
                 "action": {"type": "message",
                            "label": "個股詳細分析 →",
                            "text": "/detail"},
                 "style": "primary", "height": "sm", "flex": 2,
                 "color": "#1D9E75"},
            ]
        }
    }


def build_stock_card(h: dict, t: dict) -> dict:
    """單一股票詳細分析卡片"""
    sym   = h["symbol"]
    color, label, bg = _signal(t)
    price = t.get("price", h.get("price", 0))
    rsi   = t.get("rsi", "--")
    bb    = t.get("bb_pct", "--")
    pct_h = t.get("pct_from_high", "--")
    sigs  = t.get("signals", [])
    mv    = h.get("market_value", 0)
    pl    = h.get("unrealized_pl", 0)
    qty   = h.get("quantity", 0)
    pl_pct = pl / (mv - pl) * 100 if (mv - pl) != 0 else 0
    pl_sign = "+" if pl >= 0 else ""
    pl_color = "#1D9E75" if pl >= 0 else "#E24B4A"

    from BLIND_SPOTS_DATA import BLIND_SPOTS
    blind = BLIND_SPOTS.get(sym, "")

    sig_contents = []
    for s in sigs[:3]:
        sig_contents.append({
            "type": "text", "text": f"• {s}",
            "size": "xs", "color": "#555555", "wrap": True
        })

    blind_box = []
    if blind:
        blind_box = [{
            "type": "box", "layout": "horizontal",
            "backgroundColor": "#FAEEDA", "paddingAll": "8px",
            "cornerRadius": "4px",
            "contents": [
                {"type": "text", "text": f"⚠ {blind}",
                 "size": "xxs", "color": "#633806", "wrap": True}
            ]
        }]

    return {
        "type": "bubble",
        "header": {
            "type": "box", "layout": "horizontal",
            "backgroundColor": color, "paddingAll": "12px",
            "contents": [
                {"type": "box", "layout": "vertical", "flex": 1,
                 "contents": [
                     {"type": "text", "text": sym,
                      "weight": "bold", "size": "xl", "color": "#FFFFFF"},
                     {"type": "text",
                      "text": f"${price}  ·  {qty:.0f}股  ·  ${mv:,.0f}",
                      "size": "xs", "color": "#FFFFFF"},
                 ]},
                {"type": "box", "layout": "vertical",
                 "backgroundColor": "#FFFFFF20",
                 "cornerRadius": "99px",
                 "paddingAll": "6px",
                 "contents": [
                     {"type": "text", "text": label,
                      "size": "xs", "color": "#FFFFFF",
                      "weight": "bold", "align": "center"}
                 ]},
            ]
        },
        "body": {
            "type": "box", "layout": "vertical",
            "paddingAll": "12px", "spacing": "sm",
            "contents": [
                {"type": "box", "layout": "horizontal",
                 "spacing": "sm", "contents": [
                     {"type": "box", "layout": "vertical", "flex": 1,
                      "backgroundColor": "#F7F7F7", "cornerRadius": "6px",
                      "paddingAll": "8px", "contents": [
                          {"type": "text", "text": "RSI",
                           "size": "xxs", "color": "#888888"},
                          {"type": "text", "text": str(rsi),
                           "size": "lg", "weight": "bold",
                           "color": "#E24B4A" if (isinstance(rsi, float) and rsi > 70) else
                                    "#1D9E75" if (isinstance(rsi, float) and rsi < 30) else "#111111"},
                      ]},
                     {"type": "box", "layout": "vertical", "flex": 1,
                      "backgroundColor": "#F7F7F7", "cornerRadius": "6px",
                      "paddingAll": "8px", "contents": [
                          {"type": "text", "text": "布林帶",
                           "size": "xxs", "color": "#888888"},
                          {"type": "text", "text": f"{bb}%",
                           "size": "lg", "weight": "bold", "color": "#111111"},
                      ]},
                     {"type": "box", "layout": "vertical", "flex": 1,
                      "backgroundColor": "#F7F7F7", "cornerRadius": "6px",
                      "paddingAll": "8px", "contents": [
                          {"type": "text", "text": "損益",
                           "size": "xxs", "color": "#888888"},
                          {"type": "text",
                           "text": f"{pl_sign}{pl_pct:.1f}%",
                           "size": "lg", "weight": "bold",
                           "color": pl_color},
                      ]},
                 ]},
                {"type": "separator"},
                *sig_contents,
                *blind_box,
            ]
        }
    }


def build_detail_carousel(holdings: list, technicals: dict) -> dict:
    """第二階段：個股卡片輪播（Carousel）"""
    # 優先顯示紅燈和黃燈
    def priority(h):
        color, _, _ = _signal(technicals.get(h["symbol"], {}))
        return {" #E24B4A": 0, "#BA7517": 1}.get(color, 2)

    sorted_h = sorted(holdings, key=priority)
    bubbles  = [build_stock_card(h, technicals.get(h["symbol"], {}))
                for h in sorted_h[:10]]

    return {"type": "carousel", "contents": bubbles}


def build_news_flex(news_data: dict) -> dict:
    """重點新聞 Flex Message"""
    items = []
    seen  = set()
    for sym, news_list in news_data.items():
        for n in news_list:
            title = (n.get("title") or "")[:60]
            url   = n.get("url") or ""
            src   = n.get("source") or n.get("publisher") or "新聞"
            if title and title not in seen and url:
                seen.add(title)
                items.append((sym, title, src, url))
            if len(items) >= 5:
                break
        if len(items) >= 5:
            break

    contents = []
    for sym, title, src, url in items:
        contents.append({
            "type": "box", "layout": "vertical",
            "paddingAll": "10px",
            "backgroundColor": "#FAFAFA",
            "cornerRadius": "8px",
            "action": {"type": "uri", "label": "查看", "uri": url},
            "contents": [
                {"type": "box", "layout": "horizontal",
                 "contents": [
                     {"type": "text", "text": sym,
                      "size": "xxs", "color": "#FFFFFF",
                      "backgroundColor": "#534AB7",
                      "paddingAll": "3px",
                      "flex": 0},
                     {"type": "text", "text": src,
                      "size": "xxs", "color": "#888888",
                      "margin": "sm"},
                 ]},
                {"type": "text", "text": title,
                 "size": "sm", "color": "#111111",
                 "wrap": True, "margin": "sm"},
            ]
        })

    return {
        "type": "bubble", "size": "giga",
        "header": {
            "type": "box", "layout": "vertical",
            "backgroundColor": "#111111", "paddingAll": "14px",
            "contents": [
                {"type": "text", "text": "持股重點新聞",
                 "weight": "bold", "size": "lg", "color": "#FFFFFF"},
                {"type": "text", "text": "點擊新聞可查看全文",
                 "size": "xs", "color": "#888888", "margin": "xs"},
            ]
        },
        "body": {
            "type": "box", "layout": "vertical",
            "paddingAll": "12px", "spacing": "sm",
            "contents": contents if contents else [
                {"type": "text", "text": "目前無最新新聞",
                 "size": "sm", "color": "#888888", "align": "center"}
            ]
        }
    }


async def push_flex(flex: dict, alt: str):
    """主動推播 Flex Message"""
    user_id = os.environ["LINE_USER_ID"]
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            PUSH_URL, headers=_headers(),
            json={"to": user_id,
                  "messages": [{"type": "flex", "altText": alt,
                                "contents": flex}]}
        )
        resp.raise_for_status()


async def reply_flex(reply_token: str, flex: dict, alt: str):
    """回覆式推播"""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            REPLY_URL, headers=_headers(),
            json={"replyToken": reply_token,
                  "messages": [{"type": "flex", "altText": alt,
                                "contents": flex}]}
        )
        resp.raise_for_status()
