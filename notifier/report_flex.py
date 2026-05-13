"""
兩階段報告 Flex Message 產生器
第一階段：總覽燈號卡片
第二階段：個股詳細分析卡片群
持股圓餅圖：純 Flex Message 實作，不消耗額外 Token
"""
import os
import re
import logging
import httpx

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
    """清除特殊字元，確保 LINE API 接受"""
    if not text:
        return ""
    # 移除控制字元和不安全字元
    text = re.sub(r'[\x00-\x1f\x7f]', '', text)
    text = text.replace('"', '"').replace("'", "'")
    return text[:max_len].strip()


def _clean_url(url: str) -> str:
    """確保 URL 是有效的 https 連結"""
    if not url:
        return ""
    url = url.strip()
    if not url.startswith("http"):
        return ""
    # 移除有問題的字元
    url = re.sub(r'[\s<>"\']', '', url)
    return url[:500]


def _signal(t: dict) -> tuple[str, str, str]:
    if not t or "error" in t:
        return "#888780", "無資料", "#F1EFE8"
    rsi   = t.get("rsi", 50) or 50
    bb    = t.get("bb_pct", 50) or 50
    price = t.get("price", 0) or 0
    ma50  = t.get("ma50", 0) or 0
    pct_h = t.get("pct_from_high", 0) or 0
    score = 0
    if price and ma50 and price > ma50: score += 1
    if rsi < 70: score += 1
    if bb < 80: score += 1
    if pct_h > -20: score += 1
    if score >= 3:   return "#1D9E75", "謹慎樂觀", "#E1F5EE"
    elif score == 2: return "#BA7517", "中性觀察", "#FAEEDA"
    else:            return "#E24B4A", "注意風險", "#FCEBEB"


# ── 第一階段：總覽燈號 ──────────────────────────────

def build_overview_flex(holdings: list, technicals: dict, date_str: str) -> dict:
    total_mv  = sum(h["market_value"] for h in holdings)
    total_pl  = sum(h["unrealized_pl"] for h in holdings)
    total_day = sum(h.get("day_change", 0) for h in holdings)
    pl_color  = "#1D9E75" if total_pl >= 0 else "#E24B4A"
    day_color = "#1D9E75" if total_day >= 0 else "#E24B4A"
    pl_sign   = "+" if total_pl >= 0 else ""
    day_sign  = "+" if total_day >= 0 else ""
    red_cnt   = sum(1 for h in holdings
                    if _signal(technicals.get(h["symbol"], {}))[0] == "#E24B4A")
    yel_cnt   = sum(1 for h in holdings
                    if _signal(technicals.get(h["symbol"], {}))[0] == "#BA7517")

    rows = []
    for h in sorted(holdings, key=lambda x: -x["market_value"]):
        sym   = h["symbol"]
        t     = technicals.get(sym, {})
        color, label, _ = _signal(t)
        rsi   = t.get("rsi", "--")
        rsi_s = f"{rsi:.1f}" if isinstance(rsi, float) else str(rsi)
        rows.append({
            "type": "box", "layout": "horizontal",
            "contents": [
                {"type": "box", "layout": "vertical",
                 "width": "8px", "backgroundColor": color,
                 "contents": [{"type": "filler"}]},
                {"type": "box", "layout": "horizontal",
                 "paddingAll": "8px", "flex": 1,
                 "contents": [
                     {"type": "text", "text": sym,
                      "weight": "bold", "size": "sm",
                      "color": "#111111", "flex": 2},
                     {"type": "text", "text": label,
                      "size": "xs", "color": color, "flex": 3},
                     {"type": "text", "text": f"RSI {rsi_s}",
                      "size": "xs", "color": "#888888",
                      "align": "end", "flex": 2},
                 ]},
            ],
            "backgroundColor": "#FFFFFF",
            "borderWidth": "0.5px", "borderColor": "#F0F0F0",
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
                     "weight": "bold", "size": "lg",
                     "color": "#FFFFFF", "flex": 1},
                    {"type": "text", "text": date_str,
                     "size": "xs", "color": "#888888", "align": "end"},
                ]},
                {"type": "text",
                 "text": f"🔴 {red_cnt}檔注意  🟡 {yel_cnt}檔觀察  ✅ {len(holdings)-red_cnt-yel_cnt}檔正常",
                 "size": "xs", "color": "#AAAAAA", "margin": "sm"},
            ]
        },
        "body": {
            "type": "box", "layout": "vertical",
            "paddingAll": "12px", "spacing": "sm",
            "contents": [
                {"type": "box", "layout": "horizontal",
                 "spacing": "sm", "contents": [
                     {"type": "box", "layout": "vertical", "flex": 1,
                      "backgroundColor": "#F7F7F7",
                      "cornerRadius": "8px", "paddingAll": "10px",
                      "contents": [
                          {"type": "text", "text": "總市值",
                           "size": "xs", "color": "#888888"},
                          {"type": "text", "text": f"${total_mv:,.0f}",
                           "size": "md", "weight": "bold", "color": "#111111"},
                      ]},
                     {"type": "box", "layout": "vertical", "flex": 1,
                      "backgroundColor": "#F7F7F7",
                      "cornerRadius": "8px", "paddingAll": "10px",
                      "contents": [
                          {"type": "text", "text": "未實現損益",
                           "size": "xs", "color": "#888888"},
                          {"type": "text",
                           "text": f"{pl_sign}${abs(total_pl):,.0f}",
                           "size": "md", "weight": "bold", "color": pl_color},
                      ]},
                     {"type": "box", "layout": "vertical", "flex": 1,
                      "backgroundColor": "#F7F7F7",
                      "cornerRadius": "8px", "paddingAll": "10px",
                      "contents": [
                          {"type": "text", "text": "今日損益",
                           "size": "xs", "color": "#888888"},
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


# ── 第二階段：個股卡片 ──────────────────────────────

def build_stock_card(h: dict, t: dict) -> dict:
    sym      = h["symbol"]
    color, label, _ = _signal(t)
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
    price_s  = f"${price:.2f}" if isinstance(price, float) else f"${price}"

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
         "spacing": "xs", "contents": [
             {"type": "box", "layout": "vertical", "flex": 1,
              "backgroundColor": "#F7F7F7",
              "cornerRadius": "6px", "paddingAll": "8px",
              "contents": [
                  {"type": "text", "text": "RSI",
                   "size": "xxs", "color": "#888888"},
                  {"type": "text", "text": rsi_s,
                   "size": "md", "weight": "bold",
                   "color": "#E24B4A" if (isinstance(rsi, float) and rsi > 70)
                            else "#1D9E75" if (isinstance(rsi, float) and rsi < 30)
                            else "#111111"},
              ]},
             {"type": "box", "layout": "vertical", "flex": 1,
              "backgroundColor": "#F7F7F7",
              "cornerRadius": "6px", "paddingAll": "8px",
              "contents": [
                  {"type": "text", "text": "布林帶",
                   "size": "xxs", "color": "#888888"},
                  {"type": "text", "text": bb_s,
                   "size": "md", "weight": "bold",
                   "color": "#111111"},
              ]},
             {"type": "box", "layout": "vertical", "flex": 1,
              "backgroundColor": "#F7F7F7",
              "cornerRadius": "6px", "paddingAll": "8px",
              "contents": [
                  {"type": "text", "text": "損益%",
                   "size": "xxs", "color": "#888888"},
                  {"type": "text",
                   "text": f"{pl_sign}{pl_pct:.1f}%",
                   "size": "md", "weight": "bold",
                   "color": pl_color},
              ]},
             {"type": "box", "layout": "vertical", "flex": 1,
              "backgroundColor": "#F7F7F7",
              "cornerRadius": "6px", "paddingAll": "8px",
              "contents": [
                  {"type": "text", "text": "距高點",
                   "size": "xxs", "color": "#888888"},
                  {"type": "text", "text": pct_s,
                   "size": "md", "weight": "bold",
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
        c, _, _ = _signal(technicals.get(h["symbol"], {}))
        return {"#E24B4A": 0, "#BA7517": 1}.get(c, 2)
    sorted_h = sorted(holdings, key=priority)
    bubbles  = [build_stock_card(h, technicals.get(h["symbol"], {}))
                for h in sorted_h[:10]]
    return {"type": "carousel", "contents": bubbles}


# ── 持股分布圓餅圖 ──────────────────────────────────

def build_holdings_pie_flex(holdings: list) -> dict:
    """
    用 Flex Message 製作持股分布圓餅圖
    完全不需要 AI，零額外 Token 消耗
    """
    total = sum(h["market_value"] for h in holdings)
    if total == 0:
        return {"type": "bubble",
                "body": {"type": "box", "layout": "vertical",
                         "contents": [{"type": "text",
                                       "text": "尚無持股資料"}]}}

    # 按市值排序，前8檔獨立顯示，其餘合併
    sorted_h = sorted(holdings, key=lambda x: -x["market_value"])
    top8     = sorted_h[:8]
    others   = sorted_h[8:]

    items = []
    for i, h in enumerate(top8):
        pct = h["market_value"] / total * 100
        items.append({
            "sym":   h["symbol"],
            "mv":    h["market_value"],
            "pl":    h["unrealized_pl"],
            "pct":   pct,
            "color": COLORS[i % len(COLORS)],
        })
    if others:
        other_mv = sum(h["market_value"] for h in others)
        items.append({
            "sym":   "其他",
            "mv":    other_mv,
            "pl":    sum(h["unrealized_pl"] for h in others),
            "pct":   other_mv / total * 100,
            "color": "#CCCCCC",
        })

    total_pl   = sum(h["unrealized_pl"] for h in holdings)
    pl_sign    = "+" if total_pl >= 0 else ""
    pl_color   = "#1D9E75" if total_pl >= 0 else "#E24B4A"

    # 用進度條模擬圓餅圖（LINE Flex 不支援真正的圓餅，但進度條視覺效果佳）
    bar_contents = []
    for item in items:
        pct_w = max(1, int(item["pct"]))
        pl_s  = "+" if item["pl"] >= 0 else ""
        pl_c  = "#1D9E75" if item["pl"] >= 0 else "#E24B4A"
        bar_contents.append({
            "type": "box", "layout": "vertical",
            "margin": "sm",
            "contents": [
                {"type": "box", "layout": "horizontal",
                 "contents": [
                     {"type": "box", "layout": "vertical",
                      "width": "12px", "height": "12px",
                      "backgroundColor": item["color"],
                      "cornerRadius": "2px",
                      "contents": [], "margin": "none"},
                     {"type": "text", "text": item["sym"],
                      "size": "sm", "weight": "bold",
                      "color": "#111111", "flex": 2, "margin": "sm"},
                     {"type": "text",
                      "text": f"${item['mv']:,.0f}",
                      "size": "sm", "color": "#555555",
                      "flex": 3, "align": "end"},
                     {"type": "text",
                      "text": f"{item['pct']:.1f}%",
                      "size": "sm", "color": "#888888",
                      "flex": 1, "align": "end"},
                 ], "alignItems": "center"},
                # 進度條
                {"type": "box", "layout": "horizontal",
                 "height": "8px", "margin": "xs",
                 "backgroundColor": "#F0F0F0",
                 "cornerRadius": "4px",
                 "contents": [
                     {"type": "box", "layout": "vertical",
                      "flex": pct_w,
                      "backgroundColor": item["color"],
                      "cornerRadius": "4px",
                      "contents": []},
                     {"type": "filler",
                      "flex": max(1, 100 - pct_w)},
                 ]},
                # 損益小標
                {"type": "text",
                 "text": f"損益 {pl_s}${abs(item['pl']):,.0f}",
                 "size": "xxs", "color": pl_c,
                 "margin": "xs"},
            ]
        })

    return {
        "type": "bubble", "size": "giga",
        "header": {
            "type": "box", "layout": "vertical",
            "backgroundColor": "#111111", "paddingAll": "14px",
            "contents": [
                {"type": "text", "text": "持股分布",
                 "weight": "bold", "size": "lg", "color": "#FFFFFF"},
                {"type": "box", "layout": "horizontal",
                 "margin": "sm", "contents": [
                     {"type": "text",
                      "text": f"總市值 ${total:,.0f}",
                      "size": "sm", "color": "#CCCCCC", "flex": 1},
                     {"type": "text",
                      "text": f"損益 {pl_sign}${abs(total_pl):,.0f}",
                      "size": "sm", "color": pl_color,
                      "align": "end"},
                 ]},
            ]
        },
        "body": {
            "type": "box", "layout": "vertical",
            "paddingAll": "14px", "spacing": "none",
            "contents": bar_contents,
        }
    }


# ── 重點新聞 ────────────────────────────────────────

def build_news_flex(news_data: dict) -> dict:
    """重點新聞 Flex Message：中文標題 + 精華摘要，無連結"""
    items = []
    seen  = set()

    for sym, news_list in news_data.items():
        if not isinstance(news_list, list):
            continue
        for n in news_list:
            if not isinstance(n, dict):
                continue
            # 優先用中文標題，沒有就用原文
            title   = _clean_text(n.get("title_zh") or n.get("title") or "", 50)
            summary = _clean_text(n.get("summary_zh") or "", 100)
            src     = _clean_text(n.get("source") or n.get("publisher") or "新聞", 20)

            if not title or title in seen:
                continue
            seen.add(title)
            items.append((sym, title, summary, src))
            if len(items) >= 5:
                break
        if len(items) >= 5:
            break

    if not items:
        return {
            "type": "bubble", "size": "giga",
            "body": {
                "type": "box", "layout": "vertical",
                "paddingAll": "20px",
                "contents": [
                    {"type": "text", "text": "持股重點新聞",
                     "weight": "bold", "size": "lg"},
                    {"type": "text", "text": "目前無最新新聞資料",
                     "size": "sm", "color": "#888888", "margin": "md"},
                ]
            }
        }

    news_boxes = []
    for sym, title, summary, src in items:
        contents = [
            # 股票標籤 + 來源（用 box 包 text 實現背景色）
            {"type": "box", "layout": "horizontal",
             "spacing": "sm", "alignItems": "center",
             "contents": [
                 {"type": "box", "layout": "vertical",
                  "backgroundColor": "#534AB7",
                  "paddingStart": "6px", "paddingEnd": "6px",
                  "paddingTop": "3px", "paddingBottom": "3px",
                  "cornerRadius": "3px", "flex": 0,
                  "contents": [
                      {"type": "text", "text": sym,
                       "size": "xxs", "color": "#FFFFFF"}
                  ]},
                 {"type": "text", "text": src,
                  "size": "xxs", "color": "#888888"},
             ]},
            # 中文標題
            {"type": "text", "text": title,
             "size": "sm", "weight": "bold",
             "color": "#111111", "wrap": True,
             "margin": "sm", "maxLines": 2},
        ]
        # 有摘要就顯示
        if summary:
            contents.append({
                "type": "text", "text": summary,
                "size": "xs", "color": "#555555",
                "wrap": True, "margin": "sm",
                "maxLines": 4,
            })

        news_boxes.append({
            "type": "box", "layout": "vertical",
            "margin": "md", "paddingAll": "12px",
            "backgroundColor": "#F8F8F8",
            "cornerRadius": "8px",
            "contents": contents,
        })

    return {
        "type": "bubble", "size": "giga",
        "header": {
            "type": "box", "layout": "vertical",
            "backgroundColor": "#111111", "paddingAll": "14px",
            "contents": [
                {"type": "text", "text": "🗞 持股重點新聞",
                 "weight": "bold", "size": "lg", "color": "#FFFFFF"},
                {"type": "text", "text": "AI 中文摘要 · 不含連結",
                 "size": "xs", "color": "#888888", "margin": "xs"},
            ]
        },
        "body": {
            "type": "box", "layout": "vertical",
            "paddingAll": "12px",
            "contents": news_boxes,
        }
    }


# ── 推播函式 ────────────────────────────────────────


# ── 社群情緒條狀圖 ──────────────────────────────────

def build_sentiment_flex(sentiment_data: dict) -> dict:
    """
    社群情緒條狀圖 Flex Message
    每檔股票一列：多頭綠條 + 空頭紅條 + 情緒分數
    零 Token 消耗
    """
    # 過濾有資料的股票，按情緒分數排序
    valid = [
        (sym, s) for sym, s in sentiment_data.items()
        if s.get("total", 0) > 0
    ]
    valid.sort(key=lambda x: -x[1].get("score", 0))

    if not valid:
        return {
            "type": "bubble", "size": "giga",
            "body": {
                "type": "box", "layout": "vertical",
                "paddingAll": "20px",
                "contents": [
                    {"type": "text", "text": "社群情緒",
                     "weight": "bold", "size": "lg"},
                    {"type": "text",
                     "text": "目前無 StockTwits 資料",
                     "size": "sm", "color": "#888888", "margin": "md"},
                ]
            }
        }

    # 整體多空統計
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
        bull  = s.get("bullish", 0)
        bear  = s.get("bearish", 0)
        total = bull + bear
        score = s.get("score", 0)

        bull_pct = int(bull / total * 100) if total > 0 else 50
        bear_pct = 100 - bull_pct

        # 確保兩邊至少有 1 格寬
        bull_flex = max(1, bull_pct)
        bear_flex = max(1, bear_pct)

        # 分數標籤
        if score > 30:
            score_color = "#1D9E75"
            score_label = f"▲ {score}"
        elif score < -30:
            score_color = "#E24B4A"
            score_label = f"▼ {abs(score)}"
        else:
            score_color = "#BA7517"
            score_label = f"  {score}"

        bar_rows.append({
            "type": "box", "layout": "vertical",
            "margin": "md",
            "contents": [
                # 股票名稱 + 分數
                {"type": "box", "layout": "horizontal",
                 "alignItems": "center",
                 "contents": [
                     {"type": "text", "text": sym,
                      "size": "sm", "weight": "bold",
                      "color": "#111111", "flex": 2},
                     {"type": "text",
                      "text": f"多{bull} / 空{bear}",
                      "size": "xxs", "color": "#888888", "flex": 3},
                     {"type": "text", "text": score_label,
                      "size": "sm", "weight": "bold",
                      "color": score_color, "align": "end", "flex": 1},
                 ]},
                # 多空雙色條狀圖
                {"type": "box", "layout": "horizontal",
                 "height": "14px", "margin": "xs",
                 "cornerRadius": "7px",
                 "contents": [
                     {"type": "box", "layout": "vertical",
                      "flex": bull_flex,
                      "backgroundColor": "#1D9E75",
                      "cornerRadius": "7px"
                      if bear_flex > 1 else "7px",
                      "contents": []},
                     {"type": "box", "layout": "vertical",
                      "flex": bear_flex,
                      "backgroundColor": "#E24B4A",
                      "cornerRadius": "7px"
                      if bull_flex > 1 else "7px",
                      "contents": []},
                 ]},
                # 百分比標示
                {"type": "box", "layout": "horizontal",
                 "margin": "xs",
                 "contents": [
                     {"type": "text",
                      "text": f"多 {bull_pct}%",
                      "size": "xxs", "color": "#1D9E75"},
                     {"type": "filler"},
                     {"type": "text",
                      "text": f"{bear_pct}% 空",
                      "size": "xxs", "color": "#E24B4A"},
                 ]},
            ]
        })

    # 整體情緒大條
    overall_bar = {
        "type": "box", "layout": "vertical",
        "margin": "sm",
        "backgroundColor": "#F7F7F7",
        "cornerRadius": "10px",
        "paddingAll": "12px",
        "contents": [
            {"type": "box", "layout": "horizontal",
             "alignItems": "center",
             "contents": [
                 {"type": "text", "text": "整體情緒",
                  "size": "sm", "color": "#555555", "flex": 1},
                 {"type": "text", "text": overall_label,
                  "size": "sm", "weight": "bold",
                  "color": overall_color, "align": "end"},
             ]},
            {"type": "box", "layout": "horizontal",
             "height": "18px", "margin": "sm",
             "cornerRadius": "9px",
             "contents": [
                 {"type": "box", "layout": "vertical",
                  "flex": overall_pct,
                  "backgroundColor": "#1D9E75",
                  "cornerRadius": "9px",
                  "contents": []},
                 {"type": "box", "layout": "vertical",
                  "flex": max(1, 100 - overall_pct),
                  "backgroundColor": "#E24B4A",
                  "cornerRadius": "9px",
                  "contents": []},
             ]},
            {"type": "box", "layout": "horizontal",
             "margin": "xs",
             "contents": [
                 {"type": "text",
                  "text": f"多頭 {total_bull} 則",
                  "size": "xxs", "color": "#1D9E75"},
                 {"type": "filler"},
                 {"type": "text",
                  "text": f"{total_bear} 則 空頭",
                  "size": "xxs", "color": "#E24B4A"},
             ]},
        ]
    }

    return {
        "type": "bubble", "size": "giga",
        "header": {
            "type": "box", "layout": "vertical",
            "backgroundColor": "#111111", "paddingAll": "14px",
            "contents": [
                {"type": "text", "text": "社群情緒分析",
                 "weight": "bold", "size": "lg", "color": "#FFFFFF"},
                {"type": "text",
                 "text": "資料來源：StockTwits · 綠=多頭 紅=空頭",
                 "size": "xs", "color": "#888888", "margin": "xs"},
            ]
        },
        "body": {
            "type": "box", "layout": "vertical",
            "paddingAll": "14px",
            "contents": [
                overall_bar,
                {"type": "separator", "margin": "lg"},
                *bar_rows,
            ]
        }
    }


async def push_flex(flex: dict, alt: str):
    user_id = os.environ["LINE_USER_ID"]
    alt = _clean_text(alt, 100) or "通知"
    payload = {"to": user_id,
                "messages": [{"type": "flex",
                               "altText": alt,
                               "contents": flex}]}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            PUSH_URL, headers=_headers(), json=payload
        )
        if resp.status_code != 200:
            # 印出完整錯誤，幫助找出哪個欄位出問題
            log.error(f"push_flex {resp.status_code}: {resp.text[:500]}")
            # 嘗試找出有問題的字串
            import json as _json
            flat = _json.dumps(flex, ensure_ascii=False)
            if len(flat) > 50000:
                log.error(f"Flex 太大：{len(flat)} bytes（上限約 50000）")
        resp.raise_for_status()


async def reply_flex(reply_token: str, flex: dict, alt: str):
    alt = _clean_text(alt, 100) or "通知"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            REPLY_URL, headers=_headers(),
            json={"replyToken": reply_token,
                  "messages": [{"type": "flex",
                                "altText": alt,
                                "contents": flex}]}
        )
        if resp.status_code != 200:
            log.error(f"reply_flex 失敗 {resp.status_code}: {resp.text[:300]}")
        resp.raise_for_status()
