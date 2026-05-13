"""
技術分析模組 - 雙來源策略
主要：yfinance（免費）
備援：直接抓 Yahoo Finance JSON API（當 yfinance 被限速時）
"""
import asyncio
import logging
import pandas as pd
import ta
import httpx

log = logging.getLogger(__name__)

YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}


async def _fetch_yahoo_direct(symbol: str) -> pd.DataFrame | None:
    """直接呼叫 Yahoo Finance Chart API，繞過 yfinance"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"interval": "1d", "range": "6mo"}
    try:
        async with httpx.AsyncClient(timeout=15, headers=YAHOO_HEADERS) as client:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                log.warning(f"{symbol} Yahoo API {resp.status_code}")
                return None
            data = resp.json()

        result = data.get("chart", {}).get("result", [])
        if not result:
            return None

        r         = result[0]
        timestamps = r.get("timestamp", [])
        q         = r.get("indicators", {}).get("quote", [{}])[0]

        df = pd.DataFrame({
            "Open":   q.get("open", []),
            "High":   q.get("high", []),
            "Low":    q.get("low", []),
            "Close":  q.get("close", []),
            "Volume": q.get("volume", []),
        }, index=pd.to_datetime(timestamps, unit="s"))
        df = df.dropna()
        log.info(f"{symbol} Yahoo API 直取成功：{len(df)} 筆")
        return df

    except Exception as e:
        log.warning(f"{symbol} Yahoo API 直取失敗：{e}")
        return None


async def _fetch_yfinance(symbol: str) -> pd.DataFrame | None:
    """用 yfinance 抓資料"""
    import yfinance as yf
    loop = asyncio.get_event_loop()
    try:
        df = await loop.run_in_executor(
            None,
            lambda: yf.download(
                symbol, period="6mo", interval="1d",
                progress=False, auto_adjust=True, timeout=15
            )
        )
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception as e:
        log.warning(f"{symbol} yfinance 失敗：{e}")
        return None


def _calc_indicators(symbol: str, df: pd.DataFrame) -> dict:
    """從 DataFrame 計算技術指標"""
    close = df["Close"].squeeze()
    vol   = df["Volume"].squeeze()
    price = float(close.iloc[-1])

    if price <= 0 or len(df) < 20:
        return {"symbol": symbol, "error": f"資料不足（{len(df)}筆）"}

    ma20 = float(ta.trend.sma_indicator(close, window=20).iloc[-1])
    ma50 = float(ta.trend.sma_indicator(close, window=min(50, len(df))).iloc[-1])
    ma200 = float(ta.trend.sma_indicator(close, window=200).iloc[-1]) \
            if len(df) >= 200 else None

    rsi = float(ta.momentum.rsi(close, window=14).iloc[-1])

    macd_obj  = ta.trend.MACD(close)
    macd_hist = float(macd_obj.macd_diff().iloc[-1])
    macd_prev = float(macd_obj.macd_diff().iloc[-2])

    bb    = ta.volatility.BollingerBands(close, window=20)
    bb_up = float(bb.bollinger_hband().iloc[-1])
    bb_lo = float(bb.bollinger_lband().iloc[-1])
    bb_pct = (price - bb_lo) / (bb_up - bb_lo) * 100 if (bb_up - bb_lo) != 0 else 50

    vol_ma   = float(vol.rolling(20).mean().iloc[-1])
    vol_last = float(vol.iloc[-1])
    vol_ratio = vol_last / vol_ma if vol_ma > 0 else 1

    n = min(252, len(close))
    w52_high = float(close.rolling(n).max().iloc[-1])
    w52_low  = float(close.rolling(n).min().iloc[-1])
    pct_high = (price - w52_high) / w52_high * 100
    pct_low  = (price - w52_low)  / w52_low  * 100

    signals = []
    if price > ma20 > ma50:
        signals.append("📈 多頭排列")
    elif price < ma20 < ma50:
        signals.append("📉 空頭排列")
    if ma200:
        signals.append("✅ 站上年線" if price > ma200 else "⚠️ 跌破年線")
    if rsi > 70:   signals.append(f"🔴 RSI超買({rsi:.1f})")
    elif rsi < 30: signals.append(f"🟢 RSI超賣({rsi:.1f})")
    else:          signals.append(f"RSI中性({rsi:.1f})")
    if macd_hist > 0 and macd_hist > macd_prev: signals.append("MACD動能續強")
    elif macd_hist < 0 and macd_hist < macd_prev: signals.append("MACD動能續弱")
    if vol_ratio > 2: signals.append(f"🔔爆量({vol_ratio:.1f}x)")
    if bb_pct > 90: signals.append("布林上軌壓力")
    elif bb_pct < 10: signals.append("布林下軌支撐")

    return {
        "symbol": symbol, "price": round(price, 2),
        "ma20": round(ma20, 2), "ma50": round(ma50, 2),
        "ma200": round(ma200, 2) if ma200 else None,
        "rsi": round(rsi, 1), "macd_hist": round(macd_hist, 4),
        "bb_pct": round(bb_pct, 1), "vol_ratio": round(vol_ratio, 2),
        "w52_high": round(w52_high, 2), "w52_low": round(w52_low, 2),
        "pct_from_high": round(pct_high, 1), "pct_from_low": round(pct_low, 1),
        "signals": signals,
    }


async def _analyze_single(symbol: str) -> dict:
    """雙來源策略：先試 yfinance，失敗改 Yahoo API 直取"""
    # 第一優先：yfinance
    df = await _fetch_yfinance(symbol)

    # 備援：Yahoo Finance API 直取
    if df is None or df.empty:
        log.info(f"{symbol} 改用 Yahoo API 直取")
        df = await _fetch_yahoo_direct(symbol)

    if df is None or df.empty:
        return {"symbol": symbol, "error": "無法取得資料"}

    try:
        return _calc_indicators(symbol, df)
    except Exception as e:
        log.error(f"{symbol} 指標計算失敗：{e}")
        return {"symbol": symbol, "error": str(e)}


async def analyze_technicals(tickers: list[str]) -> dict[str, dict]:
    """並行計算所有持股技術指標，限制並發避免被封鎖"""
    semaphore = asyncio.Semaphore(4)

    async def _limited(ticker):
        async with semaphore:
            result = await _analyze_single(ticker)
            await asyncio.sleep(0.3)
            return result

    results = await asyncio.gather(*[_limited(t) for t in tickers])
    out = {r["symbol"]: r for r in results}
    ok  = sum(1 for r in out.values() if "error" not in r)
    err = sum(1 for r in out.values() if "error" in r)
    log.info(f"技術分析完成：成功{ok} 失敗{err}")
    return out
