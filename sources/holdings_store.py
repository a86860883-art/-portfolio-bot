"""
持股資料儲存模組 - JSON 快取，替代嘉信 API
"""
import json
import logging
import os
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)
HOLDINGS_FILE = Path(os.environ.get("HOLDINGS_FILE", "holdings_cache.json"))


def save_holdings(holdings: list[dict], source: str = "screenshot"):
    data = {"holdings": holdings, "updated_at": datetime.now().isoformat(), "source": source}
    HOLDINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    log.info(f"持股已儲存：{len(holdings)} 筆")


def load_holdings() -> list[dict]:
    if not HOLDINGS_FILE.exists():
        return []
    data = json.loads(HOLDINGS_FILE.read_text())
    return data.get("holdings", [])


def get_holdings_status() -> str:
    if not HOLDINGS_FILE.exists():
        return (
            "尚未有持股資料\n\n"
            "請截圖嘉信 App 或網頁版的持股頁面，\n"
            "直接傳給我就會自動更新！"
        )
    data   = json.loads(HOLDINGS_FILE.read_text())
    dt_str = data.get("updated_at", "")
    count  = len(data.get("holdings", []))
    src    = "截圖辨識" if data.get("source") == "screenshot" else data.get("source", "")
    try:
        dt = datetime.fromisoformat(dt_str)
        time_str = dt.strftime("%Y/%m/%d %H:%M")
    except Exception:
        time_str = dt_str
    return (
        f"持股資料狀態\n"
        f"更新時間：{time_str}\n"
        f"資料來源：{src}\n"
        f"持股數量：{count} 檔\n\n"
        f"傳送新截圖可即時更新"
    )
