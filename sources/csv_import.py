"""
嘉信 CSV 持股匯入模組
支援從嘉信網頁版下載的 Individual Positions CSV
"""
import csv
import io
import logging
import re

log = logging.getLogger(__name__)


def _clean_num(val: str) -> float:
    """清除 $、,、% 後轉成 float，處理負值括號格式 ($1,234.56)"""
    if not val or val.strip() in ("--", "", "N/A"):
        return 0.0
    val = val.strip().replace("$", "").replace(",", "").replace("%", "")
    # 括號負值：($57,041.47) → -57041.47
    if val.startswith("(") and val.endswith(")"):
        val = "-" + val[1:-1]
    try:
        return float(val)
    except ValueError:
        return 0.0


def parse_schwab_csv(content: str) -> list[dict]:
    """
    解析嘉信持倉 CSV，回傳標準持股格式
    跳過標題行、空白行、現金行、總計行
    """
    lines = content.splitlines()

    # 找到欄位標題行（包含 Symbol 的那行）
    header_idx = None
    for i, line in enumerate(lines):
        if '"Symbol"' in line or 'Symbol' in line:
            header_idx = i
            break

    if header_idx is None:
        log.error("找不到 CSV 標題行")
        return []

    # 用 csv.reader 解析標題行以後的資料
    data_lines = "\n".join(lines[header_idx:])
    reader = csv.DictReader(io.StringIO(data_lines))

    holdings = []
    for row in reader:
        symbol = row.get("Symbol", "").strip().strip('"')

        # 跳過空白、現金、總計行
        if not symbol:
            continue
        if symbol.lower() in ("cash & cash investments", "positions total", "account total"):
            continue
        if symbol.startswith("Cash"):
            continue

        # 清理欄位名稱（CSV 欄位名稱可能有空格或引號）
        def get(key_contains: str) -> str:
            for k, v in row.items():
                if key_contains.lower() in k.lower():
                    return v or ""
            return ""

        qty        = _clean_num(get("Qty"))
        price      = _clean_num(get("Price") if "Price Chng" not in get("Price") else "0")
        mkt_val    = _clean_num(get("Mkt Val"))
        cost_basis = _clean_num(get("Cost Basis"))
        gain_loss  = _clean_num(get("Gain $"))
        day_chng   = _clean_num(get("Day Chng $"))

        # Price 欄位特別處理（避免抓到 Price Chng）
        price_val = 0.0
        for k, v in row.items():
            if k.strip().strip('"') == "Price":
                price_val = _clean_num(v)
                break

        if qty == 0 and mkt_val == 0:
            continue

        holdings.append({
            "symbol":        symbol.upper(),
            "quantity":      qty,
            "price":         price_val,
            "market_value":  abs(mkt_val),
            "cost_basis":    cost_basis / qty if qty > 0 and cost_basis > 0 else 0.0,
            "unrealized_pl": gain_loss,
            "day_change":    day_chng,
            "source":        "csv",
            "confidence":    "high",
        })

    log.info(f"CSV 解析完成：{len(holdings)} 筆持股")
    return holdings


def parse_schwab_csv_bytes(data: bytes) -> list[dict]:
    """從 bytes 解析（LINE Bot 上傳檔案用）"""
    # 嘉信 CSV 通常是 UTF-8，但有時有 BOM
    for enc in ["utf-8-sig", "utf-8", "cp1252"]:
        try:
            return parse_schwab_csv(data.decode(enc))
        except UnicodeDecodeError:
            continue
    log.error("CSV 編碼識別失敗")
    return []
