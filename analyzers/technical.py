"""
技術分析模組 - yfinance 取得 K 線，ta 套件計算指標
完全免費，相容所有 Python 版本
"""
import asyncio
import logging
import pandas as pd
import ta
import yfinance as yf

log = logging.getLogger(__name__)


def _analyze_single(symbol: str) -> dict:
    """同步計算單一 ticker 的技術指標"""
    try:
        df = yf.download(symbol, period="6mo", interval="1d",
                         progress=False, auto_adjust=True)
        if df.empty or len(df) < 30:
            return {"symbol": symbol, "error": "資料不足"}

        # 欄位壓平（yfinance 有時回傳 MultiIndex）
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        close = df["Close"].squeeze()
        high  = df["High"].squeeze()
        low   = df["Low"].squeeze()
        vol   = df["Volume"].squeeze()
        price = float(close.iloc[-1])

        # ── 趨勢：移動平均 ──────────────────────────
        ma20  = float(ta.trend.sma_indicator(close, window=20).iloc[-1])
        ma50  = float(ta.trend.sma_indicator(close, window=50).iloc[-1])
        ma200 = float(ta.trend.sma_indicator(close, window=200).iloc[-1]) \
                if len(df) >= 200 else None

        # ── 動能：RSI ──────────────────────────────
        rsi = float(ta.momentum.rsi(close, window=14).iloc[-1])

        # ── 動能：MACD ─────────────────────────────
        macd_obj       = ta.trend.MACD(close)
        macd_hist      = float(macd_obj.macd_diff().iloc[-1])
        macd_hist_prev = float(macd_obj.macd_diff().iloc[-2])

        # ── 波動：布林通道 ─────────────────────────
        bb     = ta.volatility.BollingerBands(close, window=20)
        bb_up  = float(bb.bollinger_hband().iloc[-1])
        bb_lo  = float(bb.bollinger_lband().iloc[-1])
        bb_pct = (price - bb_lo) / (bb_up - bb_lo) * 100 \
                 if (bb_up - bb_lo) != 0 else 50

        # ── 成交量 ─────────────────────────────────
        vol_ma20  = float(vol.rolling(20).mean().iloc[-1])
        vol_today = float(vol.iloc[-1])
        vol_ratio = vol_today / vol_ma20 if vol_ma20 else 1

        # ── 52 週高低 ──────────────────────────────
        w52_high      = float(close.rolling(252).max().iloc[-1])
        w52_low       = float(close.rolling(252).min().iloc[-1])
        pct_from_high = (price - w52_high) / w52_high * 100
        pct_from_low  = (price - w52_low)  / w52_low  * 100

        # ── 訊號解讀 ───────────────────────────────
        signals = []

        if price > ma20 > ma50:
            signals.append("📈 多頭排列（價格 > MA20 > MA50）")
        elif price < ma20 < ma50:
            signals.append("📉 空頭排列（價格 < MA20 < MA50）")

        if ma200:
            signals.append("✅ 站上年線" if price > ma200 else "⚠️ 跌破年線")

        if rsi > 70:
            signals.append(f"🔴 RSI 超買（{rsi:.1f}）")
        elif rsi < 30:
            signals.append(f"🟢 RSI 超賣（{rsi:.1f}）")
        else:
            signals.append(f"RSI 中性（{rsi:.1f}）")

        if macd_hist > 0 and macd_hist > macd_hist_prev:
            signals.append("MACD 動能續強")
        elif macd_hist < 0 and macd_hist < macd_hist_prev:
            signals.append("MACD 動能續弱")
        elif macd_hist > 0 and macd_hist < macd_hist_prev:
            signals.append("MACD 黃金交叉後走弱")
        else:
            signals.append("MACD 死亡交叉後反彈")

        if vol_ratio > 2:
            signals.append(f"🔔 成交量爆量（{vol_ratio:.1f}x 均量）")

        if bb_pct > 90:
            signals.append("布林通道上軌壓力區")
        elif bb_pct < 10:
            signals.append("布林通道下軌支撐區")

        return {
            "symbol":        symbol,
            "price":         round(price, 2),
            "ma20":          round(ma20, 2),
            "ma50":          round(ma50, 2),
            "ma200":         round(ma200, 2) if ma200 else None,
            "rsi":           round(rsi, 1),
            "macd_hist":     round(macd_hist, 4),
            "bb_pct":        round(bb_pct, 1),
            "vol_ratio":     round(vol_ratio, 2),
            "w52_high":      round(w52_high, 2),
            "w52_low":       round(w52_low, 2),
            "pct_from_high": round(pct_from_high, 1),
            "pct_from_low":  round(pct_from_low, 1),
            "signals":       signals,
        }

    except Exception as e:
        log.warning(f"技術分析 {symbol} 失敗：{e}")
        return {"symbol": symbol, "error": str(e)}


async def analyze_technicals(tickers: list[str]) -> dict[str, dict]:
    """並行計算所有持股的技術指標"""
    loop = asyncio.get_event_loop()
    tasks = [
        loop.run_in_executor(None, _analyze_single, ticker)
        for ticker in tickers
    ]
    results_list = await asyncio.gather(*tasks)
    results = {r["symbol"]: r for r in results_list}
    log.info(f"技術分析完成：{list(results.keys())}")
    return results
