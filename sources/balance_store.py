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


def save_balance(data: dict):
    data["updated_at"] = datetime.now().isoformat()
    BALANCE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    log.info(f"帳戶資訊已儲存：淨值 ${data.get('net_value', 0):,.0f}")


def load_balance() -> dict:
    if not BALANCE_FILE.exists():
        return {}
    try:
        return json.loads(BALANCE_FILE.read_text())
    except Exception:
        return {}


def calc_leverage(balance: dict, total_market_value: float) -> dict:
    """
    計算槓桿倍率與風險等級
    Returns: { ratio, level, color, margin, net_value, updated_at }
    """
    margin    = balance.get("margin_balance", 0)
    net_value = balance.get("net_value", 0)
    updated   = balance.get("updated_at", "")

    if not balance or net_value <= 0:
        return {
            "ratio":     None,
            "level":     "待更新帳戶資訊",
            "color":     "#888888",
            "margin":    0,
            "net_value": 0,
            "updated_at": "",
        }

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
    except Exception:
        updated_str = updated[:10] if updated else ""

    return {
        "ratio":     round(ratio, 2),
        "level":     level,
        "color":     color,
        "margin":    margin,
        "net_value": net_value,
        "updated_at": updated_str,
    }
