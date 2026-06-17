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
    （現金/融資資訊另由 extract_account_summary() 取得）
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


def extract_account_summary(content: str) -> dict:
    """
    從嘉信 CSV 的「Cash & Cash Investments」與「Positions Total」行
    推算帳戶淨值與融資借款，不需額外上傳帳戶截圖。

    嘉信 CSV 中：
    - "Cash & Cash Investments" 的 Mkt Val 若為負值，代表融資借款金額
      （例如 -$90,731.90 表示融資借款 $90,731.90）
    - "Positions Total" 的 Mkt Val 為「持股市值加總 + 現金（含負值）」
      也就是帳戶淨清倉價值

    Returns:
        {
            "net_value": float,          # 淨清倉價值（Positions Total Mkt Val）
            "margin_balance": float,     # 融資借款金額（正數，取絕對值）
            "cash_value": float,         # 原始現金值（可能為負）
            "total_market_value": float, # 持股市值加總（不含現金）
            "found": bool,               # 是否成功抓到資料
        }
        若 CSV 中找不到這兩行，found=False，其餘欄位為 0
    """
    lines = content.splitlines()

    header_idx = None
    for i, line in enumerate(lines):
        if '"Symbol"' in line or 'Symbol' in line:
            header_idx = i
            break

    if header_idx is None:
        return {"net_value": 0.0, "margin_balance": 0.0,
                "cash_value": 0.0, "total_market_value": 0.0, "found": False}

    data_lines = "\n".join(lines[header_idx:])
    reader = csv.DictReader(io.StringIO(data_lines))

    cash_value  = 0.0
    net_value   = 0.0
    found_cash  = False
    found_total = False

    for row in reader:
        symbol = row.get("Symbol", "").strip().strip('"')

        def get(key_contains: str) -> str:
            for k, v in row.items():
                if key_contains.lower() in k.lower():
                    return v or ""
            return ""

        if symbol.lower() == "cash & cash investments":
            cash_value = _clean_num(get("Mkt Val"))
            found_cash = True
        elif symbol.lower() in ("positions total", "account total"):
            net_value = _clean_num(get("Mkt Val"))
            found_total = True

    margin_balance = abs(cash_value) if cash_value < 0 else 0.0
    total_market_value = net_value - cash_value  # 持股市值 = 淨值 - 現金(含負值融資)

    return {
        "net_value":          net_value,
        "margin_balance":     margin_balance,
        "cash_value":          cash_value,
        "total_market_value": total_market_value,
        "found":              found_cash or found_total,
    }


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


def extract_account_summary_bytes(data: bytes) -> dict:
    """從 bytes 解析帳戶摘要（淨值/融資），與 parse_schwab_csv_bytes 搭配使用"""
    for enc in ["utf-8-sig", "utf-8", "cp1252"]:
        try:
            return extract_account_summary(data.decode(enc))
        except UnicodeDecodeError:
            continue
    log.error("CSV 編碼識別失敗（帳戶摘要）")
    return {"net_value": 0.0, "margin_balance": 0.0,
            "cash_value": 0.0, "total_market_value": 0.0, "found": False}
