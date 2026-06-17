"""
帳戶資金資訊儲存模組
獨立於持股 CSV，由帳戶總結截圖辨識後儲存
"""
import json
import logging
import os
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)
BALANCE_FILE = Path(os.environ.get("BALANCE_FILE", "/tmp/balance_cache.json"))


def save_balance(data: dict, source: str = "ocr"):
    """
    儲存帳戶資訊。
    source: "ocr"（帳戶截圖辨識）或 "csv"（嘉信 CSV Positions Total 推算）
    CSV 來源通常更即時且精確（每次持股更新都會同步），優先信任。
    """
    data = dict(data)
    data["updated_at"] = datetime.now().isoformat()
    data["source"] = source
    BALANCE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    log.info(f"帳戶資訊已儲存（來源:{source}）：淨值 ${data.get('net_value', 0):,.0f}")


def load_balance() -> dict:
    if not BALANCE_FILE.exists():
        return {}
    try:
        return json.loads(BALANCE_FILE.read_text())
    except Exception:
        return {}


def calc_leverage(balance: dict, total_market_value: float) -> dict:
    """
    計算槓桿倍率與風險等級。

    淨清倉價值會即時重新計算，而非沿用上傳當天的舊快照：
        今日淨值 = 今日持股市值（即時股價） - 融資借款（沿用上次更新的快照）

    融資借款（margin_balance）在沒有額外操作（補錢/借更多/還款）的情況下
    短期內變化不大，因此延續快取中的值；但市值每天用最新股價重新計算，
    使淨值與槓桿能隨股價每日自動更新，不必每天重新上傳 CSV 或截圖。

    Returns: { ratio, level, color, margin, net_value, updated_at, source, is_stale }
    """
    margin    = balance.get("margin_balance", 0)
    cached_nv = balance.get("net_value", 0)
    updated   = balance.get("updated_at", "")
    source    = balance.get("source", "")

    if not balance or cached_nv <= 0:
        return {
            "ratio":     None,
            "level":     "待更新帳戶資訊",
            "color":     "#888888",
            "margin":    0,
            "net_value": 0,
            "updated_at": "",
            "source":    "",
            "is_stale":  False,
        }

    # 即時淨值 = 今日持股市值 - 融資借款（margin 延續快取，市值即時計算）
    net_value = total_market_value - margin
    if net_value <= 0:
        # 防呆：異常情況（例如 margin 快取過舊或資料有誤）退回快取淨值
        net_value = cached_nv

    # 槓桿 = 持股市值 ÷ 帳戶淨值
    ratio = total_market_value / net_value if net_value > 0 else 1.0

    if ratio < 1.2:
        level, color = "低風險", "#1D9E75"
    elif ratio < 1.5:
        level, color = "中等風險", "#BA7517"
    elif ratio < 2.0:
        level, color = "注意風險", "#E24B4A"
    else:
        level, color = "高槓桿警示", "#CC0000"

    try:
        dt = datetime.fromisoformat(updated)
        updated_str = dt.strftime("%m/%d %H:%M")
        # 超過 7 天沒更新融資餘額，提示可能已過時（例如你後續有額外還款/借款）
        is_stale = (datetime.now() - dt).days >= 7
    except Exception:
        updated_str = updated[:10] if updated else ""
        is_stale = False

    return {
        "ratio":     round(ratio, 2),
        "level":     level,
        "color":     color,
        "margin":    margin,
        "net_value": net_value,
        "updated_at": updated_str,
        "source":    source,
        "is_stale":  is_stale,
    }
