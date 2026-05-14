"""
Flex Message 產生器 - 全視覺化版本
"""
import os, re, logging, httpx

log = logging.getLogger(__name__)

PUSH_URL  = "https://api.line.me/v2/bot/message/push"
REPLY_URL = "https://api.line.me/v2/bot/message/reply"

COLORS = ["#534AB7","#1D9E75","#E24B4A","#185FA5","#BA7517",
          "#D4537E","#0F6E56","#888780","#C07000","#3C3489"]


def _headers():
    return {
        "Authorization": f"Bearer {os.environ['LINE_CHANNEL_ACCESS_TOKEN']}",
        "Content-Type": "application/json",
    }

def _clean_text(text: str, max_len: int = 60) -> str:
    if not text: return ""
    text = re.sub(r'[\x00-\x1f\x7f]', '', str(text))
    return text[:max_len].strip()

def _clean_url(url: str) -> str:
    if not url: return ""
    url = url.strip()
    if not url.startswith("http"): return ""
    return re.sub(r'[\s<>"\']', '', url)[:500]

def _signal(t: dict) -> tuple[str, str]:
    if not t or "error" in t:
        return "#888780", "無資料"
    rsi   = t.get("rsi", 50) or 50
    bb    = t.get("bb_pct", 50) or 50
    price = t.get("price", 0) or 0
    ma50  = t.get("ma50", 0) or 0
    pct_h = t.get("pct_from_high", 0) or 0
    score = sum([
        price > ma50 if (price and ma50) else False,
        rsi < 70,
        bb < 80,
        pct_h > -20,
    ])
    if score >= 3:   return "#1D9E75", "謹慎樂觀"
    elif score == 2: return "#BA7517", "中性觀察"
    else:            return "#E24B4A", "注意風險"


# ── 第一階段：總覽（三色燈號版）──────────────────────

def build_overview_flex(holdings: list, technicals: dict,
                        date_str: str, balance: dict = None) -> dict:
    """
    簡潔三區塊總覽：
    - 帳戶摘要（淨值、市值、損益）
    - 三色燈號分組（紅/黃/綠）
    - 槓桿倍率與風險評級
    """
    from sources.balance_store import calc_leverage

    total_mv  = sum(h["market_value"] for h in holdings)
    total_pl  = sum(h["unrealized_pl"] for h in holdings)
    pl_sign   = "+" if total_pl >= 0 else ""
    pl_color  = "#1D9E75" if total_pl >= 0 else "#E24B4A"

    lev = calc_leverage(balance or {}, total_mv)
    net_val = lev["net_value"] if lev["net_value"] > 0 else total_mv

    # 三色分組
    red, yellow, green = [], [], []
    for h in sorted(holdings, key=lambda x: -x["market_value"]):
        sym = h["symbol"]
        t   = technicals.get(sym, {})
        color, _ = _signal(t)
        if color == "#E24B4A":   red.append(sym)
        elif color == "#BA7517": yellow.append(sym)
        else:                    green.append(sym)

    def group_box(label: str, color: str, syms: list) -> dict:
        if not syms:
            syms_text = "（無）"
        else:
            syms_text = "  ".join(syms)
        return {
            "type": "box", "layout": "vertical",
            "backgroundColor": color + "18",
            "cornerRadius": "8px",
            "paddingAll": "10px", "margin": "sm",
            "contents": [
                {"type": "box", "layout": "horizontal",
                 "contents": [
                     {"type": "box", "layout": "vertical",
                      "width": "8px", "height": "8px",
                      "backgroundColor": color,
                      "cornerRadius": "4px",
                      "contents": [], "margin": "none"},
                     {"type": "text", "text": label,
                      "size": "xs", "color": color,
                      "weight": "bold", "margin": "sm"},
                     {"type": "text",
                      "text": f"{len(syms)} 檔",
                      "size": "xs", "color": color,
                      "align": "end"},
                 ], "alignItems": "center"},
                {"type": "text", "text": syms_text,
                 "size": "sm", "color": "#333333",
                 "wrap": True, "margin": "sm",
                 "weight": "bold"},
            ]
        }

    # 槓桿區塊
    lev_ratio_text = f"{lev['ratio']}×" if lev["ratio"] else "待更新帳戶資訊"
    lev_color  = lev["color"]
    lev_level  = lev["level"]
    lev_update = f"更新於 {lev['updated_at']}" if lev.get("updated_at") else "傳帳戶截圖更新"

    lev_box = {
        "type": "box", "layout": "horizontal",
        "backgroundColor": "#F7F7F7",
        "cornerRadius": "8px", "paddingAll": "12px",
        "margin": "sm",
        "contents": [
            {"type": "box", "layout": "vertical", "flex": 1,
             "contents": [
                 {"type": "text", "text": "帳戶槓桿倍率",
                  "size": "xs", "color": "#888888"},
                 {"type": "text", "text": lev_ratio_text,
                  "size": "xl", "weight": "bold",
                  "color": lev_color},
                 {"type": "text", "text": lev_level,
                  "size": "xs", "color": lev_color,
                  "margin": "xs"},
             ]},
            {"type": "box", "layout": "vertical", "flex": 1,
             "contents": [
                 {"type": "text", "text": "淨清倉價值",
                  "size": "xs", "color": "#888888"},
                 {"type": "text",
                  "text": f"${net_val:,.0f}" if net_val else "待更新",
                  "size": "md", "weight": "bold", "color": "#111111"},
                 {"type": "text", "text": lev_update,
                  "size": "xxs", "color": "#AAAAAA", "margin": "xs"},
             ]},
        ]
    }

    return {
        "type": "bubble", "size": "giga",
        "header": {
            "type": "box", "layout": "vertical",
            "backgroundColor": "#111111", "paddingAll": "14px",
            "contents": [
                {"type": "box", "layout": "horizontal", "contents": [
                    {"type": "text", "text": "持股健檢總覽",
                     "weight": "bold", "size": "lg",
                     "color": "#FFFFFF", "flex": 1},
                    {"type": "text", "text": date_str,
                     "size": "xs", "color": "#888888", "align": "end"},
                ]},
                {"type": "box", "layout": "horizontal",
                 "margin": "sm", "contents": [
                     {"type": "text",
                      "text": f"持股市值 ${total_mv:,.0f}",
                      "size": "sm", "color": "#CCCCCC", "flex": 1},
                     {"type": "text",
                      "text": f"損益 {pl_sign}${abs(total_pl):,.0f}",
                      "size": "sm", "color": pl_color, "align": "end"},
                 ]},
            ]
        },
        "body": {
            "type": "box", "layout": "vertical",
            "paddingAll": "12px",
            "contents": [
                group_box("注意風險", "#E24B4A", red),
                group_box("中性觀察", "#BA7517", yellow),
                group_box("謹慎樂觀", "#1D9E75", green),
                {"type": "separator", "margin": "md"},
                lev_box,
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


# ── 第二階段：個股詳細分析 ────────────────────────────

def build_stock_card(h: dict, t: dict) -> dict:
    sym      = h["symbol"]
    color, label = _signal(t)
    price    = t.get("price") or h.get("price", 0)
    rsi      = t.get("rsi", "--")
    bb       = t.get("bb_pct", "--")
    pct_h    = t.get("pct_from_high", "--")
    sigs     = t.get("signals", [])
    mv       = h.get("market_value", 0)
    pl       = h.get("unrealized_pl", 0)
    qty      = h.get("quantity", 0)
    cost     = mv - pl
    pl_pct   = pl / cost * 100 if cost > 0 else 0
    pl_sign  = "+" if pl >= 0 else ""
    pl_color = "#1D9E75" if pl >= 0 else "#E24B4A"
    rsi_s    = f"{rsi:.1f}" if isinstance(rsi, float) else str(rsi)
    bb_s     = f"{bb:.0f}%" if isinstance(bb, float) else str(bb)
    pct_s    = f"{pct_h:.1f}%" if isinstance(pct_h, float) else str(pct_h)
    price_s  = f"${price:.2f}" if isinstance(price, (int, float)) else f"${price}"

    try:
        from BLIND_SPOTS_DATA import BLIND_SPOTS
        blind = BLIND_SPOTS.get(sym, "")
    except Exception:
        blind = ""

    sig_contents = [
        {"type": "text", "text": f"• {s}",
         "size": "xs", "color": "#555555", "wrap": True}
        for s in sigs[:3]
    ]
    blind_box = [{
        "type": "box", "layout": "horizontal",
        "backgroundColor": "#FFF3CD", "paddingAll": "8px",
        "cornerRadius": "6px", "margin": "sm",
        "contents": [
            {"type": "text", "text": f"⚠ {blind}",
             "size": "xxs", "color": "#856404", "wrap": True}
        ]
    }] if blind else []

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
                      "text": f"{price_s}  {qty:.0f}股  ${mv:,.0f}",
                      "size": "xs", "color": "#FFFFFF"},
                 ]},
                {"type": "box", "layout": "vertical",
                 "backgroundColor": "#FFFFFF30",
                 "cornerRadius": "99px", "paddingAll": "6px",
                 "justifyContent": "center",
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
                 "spacing": "xs",
                 "contents": [
                     {"type": "box", "layout": "vertical", "flex": 1,
                      "backgroundColor": "#F7F7F7", "cornerRadius": "6px",
                      "paddingAll": "8px", "contents": [
                          {"type": "text", "text": "RSI", "size": "xxs", "color": "#888888"},
                          {"type": "text", "text": rsi_s, "size": "md",
                           "weight": "bold",
                           "color": "#E24B4A" if (isinstance(rsi, float) and rsi > 70)
                                    else "#1D9E75" if (isinstance(rsi, float) and rsi < 30)
                                    else "#111111"},
                      ]},
                     {"type": "box", "layout": "vertical", "flex": 1,
                      "backgroundColor": "#F7F7F7", "cornerRadius": "6px",
                      "paddingAll": "8px", "contents": [
                          {"type": "text", "text": "布林帶", "size": "xxs", "color": "#888888"},
                          {"type": "text", "text": bb_s, "size": "md",
                           "weight": "bold", "color": "#111111"},
                      ]},
                     {"type": "box", "layout": "vertical", "flex": 1,
                      "backgroundColor": "#F7F7F7", "cornerRadius": "6px",
                      "paddingAll": "8px", "contents": [
                          {"type": "text", "text": "損益%", "size": "xxs", "color": "#888888"},
                          {"type": "text", "text": f"{pl_sign}{pl_pct:.1f}%",
                           "size": "md", "weight": "bold", "color": pl_color},
                      ]},
                     {"type": "box", "layout": "vertical", "flex": 1,
                      "backgroundColor": "#F7F7F7", "cornerRadius": "6px",
                      "paddingAll": "8px", "contents": [
                          {"type": "text", "text": "距高點", "size": "xxs", "color": "#888888"},
                          {"type": "text", "text": pct_s, "size": "md",
                           "weight": "bold",
                           "color": "#E24B4A" if (isinstance(pct_h, float) and pct_h < -20)
                                    else "#111111"},
                      ]},
                 ]},
                {"type": "separator"},
                *sig_contents,
                *blind_box,
            ]
        }
    }


def build_detail_carousel(holdings: list, technicals: dict) -> dict:
    def priority(h):
        c, _ = _signal(technicals.get(h["symbol"], {}))
        return {"#E24B4A": 0, "#BA7517": 1}.get(c, 2)
    sorted_h = sorted(holdings, key=priority)
    bubbles  = [build_stock_card(h, technicals.get(h["symbol"], {}))
                for h in sorted_h[:10]]
    return {"type": "carousel", "contents": bubbles}


# ── 持股分布（條狀圖）──────────────────────────────────

def build_holdings_pie_flex(holdings: list, balance: dict = None) -> dict:
    from sources.balance_store import calc_leverage
    total    = sum(h["market_value"] for h in holdings)
    total_pl = sum(h["unrealized_pl"] for h in holdings)
    if total == 0:
        return {"type": "bubble",
                "body": {"type": "box", "layout": "vertical",
                         "contents": [{"type": "text", "text": "尚無持股資料"}]}}

    lev = calc_leverage(balance or {}, total)
    pl_sign  = "+" if total_pl >= 0 else ""
    pl_color = "#1D9E75" if total_pl >= 0 else "#E24B4A"

    sorted_h = sorted(holdings, key=lambda x: -x["market_value"])
    top8, others = sorted_h[:8], sorted_h[8:]
    items = []
    for i, h in enumerate(top8):
        pct = h["market_value"] / total * 100
        items.append({"sym": h["symbol"], "mv": h["market_value"],
                      "pl": h["unrealized_pl"], "pct": pct,
                      "color": COLORS[i % len(COLORS)]})
    if others:
        omv = sum(h["market_value"] for h in others)
        items.append({"sym": "其他", "mv": omv,
                      "pl": sum(h["unrealized_pl"] for h in others),
                      "pct": omv / total * 100, "color": "#CCCCCC"})

    bar_contents = []
    for item in items:
        pw   = max(1, int(item["pct"]))
        pl_s = "+" if item["pl"] >= 0 else ""
        pl_c = "#1D9E75" if item["pl"] >= 0 else "#E24B4A"
        bar_contents.append({
            "type": "box", "layout": "vertical", "margin": "sm",
            "contents": [
                {"type": "box", "layout": "horizontal",
                 "alignItems": "center",
                 "contents": [
                     {"type": "box", "layout": "vertical",
                      "width": "12px", "height": "12px",
                      "backgroundColor": item["color"],
                      "cornerRadius": "2px", "contents": []},
                     {"type": "text", "text": item["sym"],
                      "size": "sm", "weight": "bold",
                      "color": "#111111", "flex": 2, "margin": "sm"},
                     {"type": "text", "text": f"${item['mv']:,.0f}",
                      "size": "sm", "color": "#555555",
                      "flex": 3, "align": "end"},
                     {"type": "text", "text": f"{item['pct']:.1f}%",
                      "size": "sm", "color": "#888888",
                      "flex": 1, "align": "end"},
                 ]},
                {"type": "box", "layout": "horizontal",
                 "height": "8px", "margin": "xs",
                 "backgroundColor": "#F0F0F0", "cornerRadius": "4px",
                 "contents": [
                     {"type": "box", "layout": "vertical", "flex": pw,
                      "backgroundColor": item["color"],
                      "cornerRadius": "4px", "contents": []},
                     {"type": "filler", "flex": max(1, 100-pw)},
                 ]},
                {"type": "text",
                 "text": f"損益 {pl_s}${abs(item['pl']):,.0f}",
                 "size": "xxs", "color": pl_c, "margin": "xs"},
            ]
        })

    lev_text = f"{lev['ratio']}× {lev['level']}" if lev["ratio"] else "待更新帳戶資訊"
    lev_color = lev["color"]

    return {
        "type": "bubble", "size": "giga",
        "header": {
            "type": "box", "layout": "vertical",
            "backgroundColor": "#111111", "paddingAll": "14px",
            "contents": [
                {"type": "text", "text": "持股分布",
                 "weight": "bold", "size": "lg", "color": "#FFFFFF"},
                {"type": "box", "layout": "horizontal", "margin": "sm",
                 "contents": [
                     {"type": "text",
                      "text": f"市值 ${total:,.0f}",
                      "size": "sm", "color": "#CCCCCC", "flex": 1},
                     {"type": "text",
                      "text": f"損益 {pl_sign}${abs(total_pl):,.0f}",
                      "size": "sm", "color": pl_color, "align": "end"},
                 ]},
                {"type": "text", "text": f"槓桿 {lev_text}",
                 "size": "xs", "color": lev_color, "margin": "xs"},
            ]
        },
        "body": {
            "type": "box", "layout": "vertical",
            "paddingAll": "14px", "spacing": "none",
            "contents": bar_contents,
        }
    }


# ── 社群情緒條狀圖 ────────────────────────────────────

def build_sentiment_flex(sentiment_data: dict) -> dict:
    valid = [(sym, s) for sym, s in sentiment_data.items() if s.get("total", 0) > 0]
    valid.sort(key=lambda x: -x[1].get("score", 0))
    if not valid:
        return {"type": "bubble", "size": "giga",
                "body": {"type": "box", "layout": "vertical",
                         "paddingAll": "20px",
                         "contents": [
                             {"type": "text", "text": "社群情緒",
                              "weight": "bold", "size": "lg"},
                             {"type": "text", "text": "目前無 StockTwits 資料",
                              "size": "sm", "color": "#888888", "margin": "md"},
                         ]}}

    total_bull = sum(s.get("bullish", 0) for _, s in valid)
    total_bear = sum(s.get("bearish", 0) for _, s in valid)
    total_all  = total_bull + total_bear
    overall_pct = int(total_bull / total_all * 100) if total_all > 0 else 50
    overall_color = "#1D9E75" if overall_pct >= 55 else \
                    "#E24B4A" if overall_pct <= 45 else "#BA7517"
    overall_label = "整體偏多" if overall_pct >= 55 else \
                    "整體偏空" if overall_pct <= 45 else "多空均衡"

    bar_rows = []
    for sym, s in valid:
        bull = s.get("bullish", 0); bear = s.get("bearish", 0)
        total = bull + bear; score = s.get("score", 0)
        bull_pct = int(bull / total * 100) if total > 0 else 50
        bear_pct = 100 - bull_pct
        score_color = "#1D9E75" if score > 30 else "#E24B4A" if score < -30 else "#BA7517"
        score_label = f"▲{score}" if score > 30 else f"▼{abs(score)}" if score < -30 else f"~{score}"
        bar_rows.append({
            "type": "box", "layout": "vertical", "margin": "md",
            "contents": [
                {"type": "box", "layout": "horizontal",
                 "alignItems": "center",
                 "contents": [
                     {"type": "text", "text": sym, "size": "sm",
                      "weight": "bold", "color": "#111111", "flex": 2},
                     {"type": "text", "text": f"多{bull}/空{bear}",
                      "size": "xxs", "color": "#888888", "flex": 3},
                     {"type": "text", "text": score_label, "size": "sm",
                      "weight": "bold", "color": score_color,
                      "align": "end", "flex": 1},
                 ]},
                {"type": "box", "layout": "horizontal",
                 "height": "14px", "margin": "xs", "cornerRadius": "7px",
                 "contents": [
                     {"type": "box", "layout": "vertical",
                      "flex": max(1, bull_pct),
                      "backgroundColor": "#1D9E75",
                      "cornerRadius": "7px", "contents": []},
                     {"type": "box", "layout": "vertical",
                      "flex": max(1, bear_pct),
                      "backgroundColor": "#E24B4A",
                      "cornerRadius": "7px", "contents": []},
                 ]},
                {"type": "box", "layout": "horizontal", "margin": "xs",
                 "contents": [
                     {"type": "text", "text": f"多 {bull_pct}%",
                      "size": "xxs", "color": "#1D9E75"},
                     {"type": "filler"},
                     {"type": "text", "text": f"{bear_pct}% 空",
                      "size": "xxs", "color": "#E24B4A"},
                 ]},
            ]
        })

    overall_bar = {
        "type": "box", "layout": "vertical", "margin": "sm",
        "backgroundColor": "#F7F7F7", "cornerRadius": "10px",
        "paddingAll": "12px",
        "contents": [
            {"type": "box", "layout": "horizontal", "alignItems": "center",
             "contents": [
                 {"type": "text", "text": "整體情緒",
                  "size": "sm", "color": "#555555", "flex": 1},
                 {"type": "text", "text": overall_label, "size": "sm",
                  "weight": "bold", "color": overall_color, "align": "end"},
             ]},
            {"type": "box", "layout": "horizontal",
             "height": "18px", "margin": "sm", "cornerRadius": "9px",
             "contents": [
                 {"type": "box", "layout": "vertical",
                  "flex": overall_pct, "backgroundColor": "#1D9E75",
                  "cornerRadius": "9px", "contents": []},
                 {"type": "box", "layout": "vertical",
                  "flex": max(1, 100-overall_pct),
                  "backgroundColor": "#E24B4A",
                  "cornerRadius": "9px", "contents": []},
             ]},
            {"type": "box", "layout": "horizontal", "margin": "xs",
             "contents": [
                 {"type": "text", "text": f"多頭 {total_bull} 則",
                  "size": "xxs", "color": "#1D9E75"},
                 {"type": "filler"},
                 {"type": "text", "text": f"{total_bear} 則 空頭",
                  "size": "xxs", "color": "#E24B4A"},
             ]},
        ]
    }

    return {
        "type": "bubble", "size": "giga",
        "header": {"type": "box", "layout": "vertical",
                   "backgroundColor": "#111111", "paddingAll": "14px",
                   "contents": [
                       {"type": "text", "text": "社群情緒分析",
                        "weight": "bold", "size": "lg", "color": "#FFFFFF"},
                       {"type": "text",
                        "text": "資料來源：StockTwits · 綠=多頭 紅=空頭",
                        "size": "xs", "color": "#888888", "margin": "xs"},
                   ]},
        "body": {"type": "box", "layout": "vertical",
                 "paddingAll": "14px",
                 "contents": [overall_bar,
                              {"type": "separator", "margin": "lg"},
                              *bar_rows]}
    }


# ── 新聞（兩區塊）──────────────────────────────────────

def build_news_flex(news_data: dict) -> dict:
    """
    兩大區塊新聞卡片：
    1. 重大市場新聞（3則）
    2. 持股相關新聞（5則）
    """
    # 相容舊格式（list）和新格式（dict）
    if isinstance(news_data, list):
        market_news = []
        stock_news  = news_data
    else:
        market_news = news_data.get("market", [])
        stock_news  = news_data.get("stocks", [])

    def news_box(n: dict, sym: str = "") -> dict:
        title   = _clean_text(n.get("title_zh") or n.get("title") or "", 45)
        summary = _clean_text(n.get("summary_zh") or "", 90)
        src     = _clean_text(n.get("publisher") or n.get("source") or "新聞", 20)
        tag_sym = sym or n.get("symbol", "")

        tag = []
        if tag_sym:
            tag = [{"type": "box", "layout": "vertical",
                    "backgroundColor": "#534AB7",
                    "paddingStart": "6px", "paddingEnd": "6px",
                    "paddingTop": "3px", "paddingBottom": "3px",
                    "cornerRadius": "3px", "flex": 0,
                    "contents": [{"type": "text", "text": tag_sym,
                                  "size": "xxs", "color": "#FFFFFF"}]}]

        contents = [
            {"type": "box", "layout": "horizontal",
             "spacing": "sm", "alignItems": "center",
             "contents": [*tag,
                          {"type": "text", "text": src,
                           "size": "xxs", "color": "#888888"}]},
            {"type": "text", "text": title if title else "(無標題)",
             "size": "sm", "weight": "bold", "color": "#111111",
             "wrap": True, "margin": "sm", "maxLines": 2},
        ]
        if summary:
            contents.append({
                "type": "text", "text": summary,
                "size": "xs", "color": "#555555",
                "wrap": True, "margin": "sm", "maxLines": 3,
            })

        return {"type": "box", "layout": "vertical",
                "margin": "md", "paddingAll": "12px",
                "backgroundColor": "#F8F8F8",
                "cornerRadius": "8px",
                "contents": contents}

    body_contents = []

    # 區塊一：重大市場新聞
    if market_news:
        body_contents.append({
            "type": "box", "layout": "horizontal",
            "contents": [
                {"type": "box", "layout": "vertical",
                 "width": "4px", "backgroundColor": "#E24B4A",
                 "cornerRadius": "2px", "contents": []},
                {"type": "text", "text": "重大市場新聞",
                 "size": "sm", "weight": "bold",
                 "color": "#111111", "margin": "sm"},
            ], "alignItems": "center"
        })
        for n in market_news[:3]:
            if not n.get("is_duplicate", False):
                body_contents.append(news_box(n))

    if market_news and stock_news:
        body_contents.append({"type": "separator", "margin": "lg"})

    # 區塊二：持股相關新聞
    if stock_news:
        body_contents.append({
            "type": "box", "layout": "horizontal",
            "contents": [
                {"type": "box", "layout": "vertical",
                 "width": "4px", "backgroundColor": "#534AB7",
                 "cornerRadius": "2px", "contents": []},
                {"type": "text", "text": "持股相關新聞",
                 "size": "sm", "weight": "bold",
                 "color": "#111111", "margin": "sm"},
            ], "alignItems": "center"
        })
        for n in stock_news[:5]:
            if not n.get("is_duplicate", False):
                body_contents.append(news_box(n, n.get("symbol", "")))

    if not body_contents:
        body_contents = [{"type": "text", "text": "目前無最新新聞資料",
                          "size": "sm", "color": "#888888", "align": "center"}]

    return {
        "type": "bubble", "size": "giga",
        "header": {"type": "box", "layout": "vertical",
                   "backgroundColor": "#111111", "paddingAll": "14px",
                   "contents": [
                       {"type": "text", "text": "🗞 持股重點新聞",
                        "weight": "bold", "size": "lg", "color": "#FFFFFF"},
                       {"type": "text",
                        "text": "AI 中文摘要 · 同公司當天最多1則",
                        "size": "xs", "color": "#888888", "margin": "xs"},
                   ]},
        "body": {"type": "box", "layout": "vertical",
                 "paddingAll": "12px", "contents": body_contents}
    }


# ── 系統 Flex ──────────────────────────────────────────

def build_success_flex(title: str, holdings: list, extra_text: str = "") -> dict:
    total  = sum(h["market_value"] for h in holdings)
    pl     = sum(h["unrealized_pl"] for h in holdings)
    sign   = "+" if pl >= 0 else ""
    color  = "#1D9E75" if pl >= 0 else "#E24B4A"
    rows   = []
    for i, h in enumerate(sorted(holdings, key=lambda x: -x["market_value"])[:8]):
        pct = h["market_value"] / total * 100 if total > 0 else 0
        pw  = max(1, int(pct))
        s   = "▲" if h["unrealized_pl"] >= 0 else "▼"
        pc  = "#1D9E75" if h["unrealized_pl"] >= 0 else "#E24B4A"
        rows.append({
            "type": "box", "layout": "vertical", "margin": "sm",
            "contents": [
                {"type": "box", "layout": "horizontal",
                 "alignItems": "center",
                 "contents": [
                     {"type": "text", "text": h["symbol"],
                      "size": "sm", "weight": "bold",
                      "color": "#111111", "flex": 2},
                     {"type": "text", "text": f"${h['market_value']:,.0f}",
                      "size": "sm", "color": "#555555",
                      "flex": 3, "align": "end"},
                     {"type": "text",
                      "text": f"{s}${abs(h['unrealized_pl']):,.0f}",
                      "size": "xs", "color": pc, "flex": 2, "align": "end"},
                 ]},
                {"type": "box", "layout": "horizontal",
                 "height": "6px", "margin": "xs",
                 "backgroundColor": "#F0F0F0", "cornerRadius": "3px",
                 "contents": [
                     {"type": "box", "layout": "vertical",
                      "flex": pw, "backgroundColor": COLORS[i % len(COLORS)],
                      "cornerRadius": "3px", "contents": []},
                     {"type": "filler", "flex": max(1, 100-pw)},
                 ]},
            ]
        })
    extra = [{"type": "text", "text": extra_text, "size": "xs",
              "color": "#888888", "margin": "md", "wrap": True}] if extra_text else []
    return {
        "type": "bubble", "size": "giga",
        "header": {"type": "box", "layout": "vertical",
                   "backgroundColor": "#1D9E75", "paddingAll": "14px",
                   "contents": [
                       {"type": "text", "text": f"✓ {title}",
                        "weight": "bold", "size": "md", "color": "#FFFFFF"},
                       {"type": "box", "layout": "horizontal", "margin": "sm",
                        "contents": [
                            {"type": "text", "text": f"市值 ${total:,.0f}",
                             "size": "sm", "color": "#FFFFFF", "flex": 1},
                            {"type": "text",
                             "text": f"損益 {sign}${abs(pl):,.0f}",
                             "size": "sm", "color": "#FFFFFF", "align": "end"},
                        ]},
                   ]},
        "body": {"type": "box", "layout": "vertical",
                 "paddingAll": "14px", "contents": rows + extra},
        "footer": {"type": "box", "layout": "horizontal",
                   "paddingAll": "10px", "spacing": "sm",
                   "contents": [
                       {"type": "button",
                        "action": {"type": "message", "label": "今日總覽",
                                   "text": "/overview"},
                        "style": "primary", "height": "sm",
                        "color": "#1D9E75", "flex": 1},
                       {"type": "button",
                        "action": {"type": "message", "label": "個股分析",
                                   "text": "/detail"},
                        "style": "secondary", "height": "sm", "flex": 1},
                   ]}
    }


def build_balance_success_flex(balance: dict) -> dict:
    """帳戶總結辨識成功卡片"""
    net    = balance.get("net_value", 0)
    margin = balance.get("margin_balance", 0)
    total  = balance.get("total_market_value", 0)
    cash   = balance.get("available_cash", 0)
    lev_ratio = total / net if net > 0 and total > 0 else 0
    lev_color = "#1D9E75" if lev_ratio < 1.2 else \
                "#BA7517" if lev_ratio < 1.5 else "#E24B4A"

    rows = [
        ("淨清倉價值", f"${net:,.0f}", "#111111"),
        ("持股總市值", f"${total:,.0f}", "#111111"),
        ("融資借款",   f"${margin:,.0f}", "#E24B4A" if margin > 0 else "#111111"),
        ("可用資金",   f"${cash:,.0f}", "#111111"),
        ("槓桿倍率",   f"{lev_ratio:.2f}×" if lev_ratio > 0 else "N/A", lev_color),
    ]
    row_boxes = [
        {"type": "box", "layout": "horizontal", "margin": "sm",
         "contents": [
             {"type": "text", "text": k, "size": "sm",
              "color": "#888888", "flex": 2},
             {"type": "text", "text": v, "size": "sm",
              "weight": "bold", "color": c, "flex": 3, "align": "end"},
         ]}
        for k, v, c in rows
    ]
    return {
        "type": "bubble",
        "header": {"type": "box", "layout": "vertical",
                   "backgroundColor": "#185FA5", "paddingAll": "14px",
                   "contents": [
                       {"type": "text", "text": "✓ 帳戶資訊已更新",
                        "weight": "bold", "size": "md", "color": "#FFFFFF"},
                       {"type": "text",
                        "text": "槓桿與淨值資訊已同步，總覽將反映最新數據",
                        "size": "xs", "color": "#CCCCCC", "margin": "xs",
                        "wrap": True},
                   ]},
        "body": {"type": "box", "layout": "vertical",
                 "paddingAll": "14px", "contents": row_boxes},
        "footer": {"type": "box", "layout": "vertical", "paddingAll": "10px",
                   "contents": [{"type": "button",
                                 "action": {"type": "message",
                                            "label": "查看今日總覽",
                                            "text": "/overview"},
                                 "style": "primary", "height": "sm",
                                 "color": "#185FA5"}]}
    }


def build_status_flex(status_text: str) -> dict:
    lines = status_text.split("\n")
    rows  = []
    for line in lines:
        if "：" in line:
            k, v = line.split("：", 1)
            rows.append({"type": "box", "layout": "horizontal", "margin": "sm",
                         "contents": [
                             {"type": "text", "text": k.strip(), "size": "sm",
                              "color": "#888888", "flex": 2},
                             {"type": "text", "text": v.strip(), "size": "sm",
                              "color": "#111111", "flex": 3, "align": "end",
                              "wrap": True},
                         ]})
    if not rows:
        rows = [{"type": "text", "text": status_text, "size": "sm",
                 "color": "#888888", "wrap": True}]
    return {
        "type": "bubble",
        "header": {"type": "box", "layout": "vertical",
                   "backgroundColor": "#111111", "paddingAll": "14px",
                   "contents": [{"type": "text", "text": "持股資料狀態",
                                  "weight": "bold", "size": "md",
                                  "color": "#FFFFFF"}]},
        "body": {"type": "box", "layout": "vertical",
                 "paddingAll": "14px", "spacing": "none",
                 "contents": rows},
        "footer": {"type": "box", "layout": "vertical", "paddingAll": "10px",
                   "contents": [{"type": "button",
                                 "action": {"type": "message",
                                            "label": "上傳 CSV 更新持股",
                                            "text": "上傳CSV說明"},
                                 "style": "secondary", "height": "sm"}]}
    }


def build_help_flex() -> dict:
    buttons = [
        ("📊", "今日總覽",  "/overview"),
        ("📈", "個股分析",  "/detail"),
        ("🗞",  "重點新聞",  "/news"),
        ("💼", "我的持股",  "/holdings"),
        ("💬", "社群情緒",  "/sentiment"),
    ]
    return {
        "type": "bubble", "size": "giga",
        "header": {"type": "box", "layout": "vertical",
                   "backgroundColor": "#111111", "paddingAll": "14px",
                   "contents": [
                       {"type": "text", "text": "美股健檢機器人",
                        "weight": "bold", "size": "lg", "color": "#FFFFFF"},
                       {"type": "text", "text": "點擊按鈕或上傳 CSV 開始使用",
                        "size": "xs", "color": "#888888", "margin": "xs"},
                   ]},
        "body": {"type": "box", "layout": "vertical", "paddingAll": "12px",
                 "contents": [
                     {"type": "text", "text": "功能選單", "size": "xs",
                      "color": "#888888", "weight": "bold"},
                     *[{"type": "button",
                        "action": {"type": "message", "label": f"{i} {l}", "text": c},
                        "style": "secondary", "height": "sm", "margin": "sm"}
                       for i, l, c in buttons],
                     {"type": "separator", "margin": "lg"},
                     {"type": "text", "text": "更新持股", "size": "xs",
                      "color": "#888888", "weight": "bold", "margin": "lg"},
                     {"type": "text",
                      "text": "傳嘉信 CSV → 持股更新\n傳帳戶總結截圖 → 槓桿/淨值更新",
                      "size": "sm", "color": "#555555",
                      "wrap": True, "margin": "sm"},
                     {"type": "separator", "margin": "lg"},
                     {"type": "text",
                      "text": "每日凌晨 5:30 自動推播健檢報告（週一至五）",
                      "size": "xs", "color": "#888888",
                      "wrap": True, "margin": "lg"},
                 ]}
    }


def build_clear_flex() -> dict:
    return {
        "type": "bubble",
        "body": {"type": "box", "layout": "vertical",
                 "paddingAll": "20px", "alignItems": "center",
                 "contents": [
                     {"type": "text", "text": "✓", "size": "5xl",
                      "color": "#1D9E75", "align": "center"},
                     {"type": "text", "text": "對話記憶已清除",
                      "weight": "bold", "size": "md", "color": "#111111",
                      "align": "center", "margin": "md"},
                     {"type": "text", "text": "有什麼想問的嗎？",
                      "size": "sm", "color": "#888888",
                      "align": "center", "wrap": True, "margin": "sm"},
                 ]},
        "footer": {"type": "box", "layout": "vertical", "paddingAll": "10px",
                   "contents": [{"type": "button",
                                 "action": {"type": "message",
                                            "label": "📊 開始健檢",
                                            "text": "/overview"},
                                 "style": "primary", "height": "sm",
                                 "color": "#1D9E75"}]}
    }


# ── 推播函式 ────────────────────────────────────────────

async def push_flex(flex: dict, alt: str):
    user_id = os.environ["LINE_USER_ID"]
    alt = _clean_text(alt, 100) or "通知"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            PUSH_URL, headers=_headers(),
            json={"to": user_id,
                  "messages": [{"type": "flex", "altText": alt,
                                "contents": flex}]}
        )
        if resp.status_code != 200:
            log.error(f"push_flex {resp.status_code}: {resp.text[:300]}")
        resp.raise_for_status()


async def reply_flex(reply_token: str, flex: dict, alt: str):
    alt = _clean_text(alt, 100) or "通知"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            REPLY_URL, headers=_headers(),
            json={"replyToken": reply_token,
                  "messages": [{"type": "flex", "altText": alt,
                                "contents": flex}]}
        )
        if resp.status_code != 200:
            log.error(f"reply_flex {resp.status_code}: {resp.text[:300]}")
        resp.raise_for_status()
