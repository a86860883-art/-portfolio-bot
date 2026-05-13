"""
LINE Flex Message 儀表板
持股分布進度條 + 帳戶概覽 + 快速功能選單
"""
import logging
import os
import httpx
from notifier.line_push import reply_flex

log = logging.getLogger(__name__)

COLORS = ["#534AB7","#185FA5","#1D9E75","#E24B4A","#BA7517","#D4537E","#0F6E56","#888780"]


def _bar(symbol: str, pct: float, color: str) -> dict:
    w = max(1, min(int(pct), 100))
    return {
        "type": "box", "layout": "vertical", "margin": "sm",
        "contents": [
            {"type": "box", "layout": "horizontal", "contents": [
                {"type": "text", "text": symbol, "size": "sm", "weight": "bold",
                 "color": "#111111", "flex": 2},
                {"type": "text", "text": f"{pct:.1f}%", "size": "sm",
                 "color": "#555555", "align": "end", "flex": 1},
            ]},
            {"type": "box", "layout": "horizontal", "height": "6px", "margin": "xs",
             "contents": [
                 {"type": "box", "layout": "vertical", "width": f"{w}%",
                  "contents": [{"type": "filler"}],
                  "backgroundColor": color, "cornerRadius": "4px"},
                 {"type": "filler"},
             ]},
        ]
    }


def _btn(label: str, icon: str, cmd: str) -> dict:
    return {
        "type": "button",
        "action": {"type": "message", "label": f"{icon} {label}", "text": cmd},
        "style": "secondary", "margin": "sm", "height": "sm",
    }


def build_flex(holdings: list[dict]) -> dict:
    total_mv   = sum(h["market_value"] for h in holdings)
    unreal_pl  = sum(h["unrealized_pl"] for h in holdings)
    pl_color   = "#1D9E75" if unreal_pl >= 0 else "#E24B4A"
    pl_sign    = "+" if unreal_pl >= 0 else ""

    dist = []
    for i, h in enumerate(sorted(holdings, key=lambda x: -x["market_value"])[:8]):
        pct = h["market_value"] / total_mv * 100 if total_mv else 0
        dist.append((h["symbol"], pct, COLORS[i % len(COLORS)]))

    return {
        "type": "bubble", "size": "giga",
        "header": {
            "type": "box", "layout": "vertical",
            "backgroundColor": "#F7F7F7", "paddingAll": "16px",
            "contents": [
                {"type": "text", "text": "持股健檢儀表板",
                 "weight": "bold", "size": "lg", "color": "#111111"},
                {"type": "text", "text": "截圖辨識版",
                 "size": "xs", "color": "#888888", "margin": "xs"},
            ]
        },
        "body": {
            "type": "box", "layout": "vertical",
            "paddingAll": "16px", "spacing": "md",
            "contents": [
                # 指標行
                {"type": "box", "layout": "horizontal", "spacing": "sm", "contents": [
                    {"type": "box", "layout": "vertical", "flex": 1,
                     "backgroundColor": "#F0F0F0", "cornerRadius": "8px", "paddingAll": "10px",
                     "contents": [
                         {"type": "text", "text": "總市值", "size": "xs", "color": "#888888"},
                         {"type": "text", "text": f"${total_mv:,.0f}",
                          "size": "md", "weight": "bold", "color": "#111111"},
                     ]},
                    {"type": "box", "layout": "vertical", "flex": 1,
                     "backgroundColor": "#F0F0F0", "cornerRadius": "8px", "paddingAll": "10px",
                     "contents": [
                         {"type": "text", "text": "未實現損益", "size": "xs", "color": "#888888"},
                         {"type": "text", "text": f"{pl_sign}${abs(unreal_pl):,.0f}",
                          "size": "md", "weight": "bold", "color": pl_color},
                     ]},
                    {"type": "box", "layout": "vertical", "flex": 1,
                     "backgroundColor": "#F0F0F0", "cornerRadius": "8px", "paddingAll": "10px",
                     "contents": [
                         {"type": "text", "text": "持股檔數", "size": "xs", "color": "#888888"},
                         {"type": "text", "text": f"{len(holdings)} 檔",
                          "size": "md", "weight": "bold", "color": "#111111"},
                     ]},
                ]},
                {"type": "separator"},
                # 持股分布
                {"type": "text", "text": "持股分布",
                 "weight": "bold", "size": "sm", "color": "#333333"},
                *[_bar(sym, pct, col) for sym, pct, col in dist],
                {"type": "separator"},
                # 功能選單
                {"type": "text", "text": "快速功能",
                 "weight": "bold", "size": "sm", "color": "#333333"},
                {"type": "box", "layout": "horizontal", "spacing": "sm", "contents": [
                    _btn("每日健檢", "📊", "/report"),
                    _btn("持股清單", "💼", "/holdings"),
                    _btn("技術訊號", "📈", "/technical"),
                ]},
                {"type": "box", "layout": "horizontal", "spacing": "sm", "contents": [
                    _btn("社群情緒", "💬", "/sentiment"),
                    _btn("資料狀態", "🔍", "/status"),
                    _btn("重置對話", "🔄", "/reset"),
                ]},
            ]
        },
        "footer": {
            "type": "box", "layout": "vertical",
            "backgroundColor": "#F7F7F7", "paddingAll": "12px",
            "contents": [{"type": "text",
                          "text": "資料來源：截圖辨識｜僅供參考，非投資建議",
                          "size": "xxs", "color": "#AAAAAA",
                          "wrap": True, "align": "center"}]
        }
    }


async def send_dashboard(reply_token: str, holdings: list[dict]):
    flex = build_flex(holdings)
    await reply_flex(reply_token, flex, "持股健檢儀表板")
    log.info("儀表板推播完成")
