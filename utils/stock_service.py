from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
import requests

from config import (
    MIN_ADX_ENTRY,
    MIN_DAILY_VALUE_B,
    MIN_ENTRY_CONDITIONS,
    MIN_MARKET_CAP_T,
    MIN_VOL_RATIO_ENTRY,
    SECTORS_API_KEY,
    SECTORS_BASE,
)
from utils.fundamental_utils import normalize_der
from utils.market_regime import get_coal_price
from utils.runtime_cache import TTLCache
from utils.scoring_rules import compute_score
from utils.sector_data import get_sector_context
from utils.ticker_utils import normalize_ticker
from utils.yf_guard import YFinanceUnavailable, get_history, get_info, get_institutional_holders


_sess = requests.Session()
_sess.headers.update({
    "User-Agent": "IDXAnalystBot/3.0",
    "Accept": "application/json",
})
if SECTORS_API_KEY:
    _sess.headers["Authorization"] = f"Bearer {SECTORS_API_KEY}"

_stock_data_cache = TTLCache[dict](max_entries=256)
STOCK_DATA_TTL_SECONDS = 45.0


def _safe(val, default=0.0):
    if val is None:
        return default
    try:
        v = float(val)
        return default if (np.isnan(v) or np.isinf(v)) else v
    except (TypeError, ValueError):
        return default


def _fmt_idr(val: float) -> str:
    av = abs(val)
    sign = "-" if val < 0 else ""
    if av >= 1e12:
        return f"{sign}{av/1e12:.2f}T"
    if av >= 1e9:
        return f"{sign}{av/1e9:.2f}B"
    if av >= 1e6:
        return f"{sign}{av/1e6:.2f}M"
    return f"{sign}{av:,.0f}"


def _fmt_lot(val: float) -> str:
    av = abs(val)
    sign = "+" if val >= 0 else "-"
    if av >= 1e6:
        return f"{sign}{av/1e6:.2f}M lot"
    if av >= 1e3:
        return f"{sign}{av/1e3:.2f}K lot"
    return f"{sign}{av:.0f} lot"


def _yf_info_with_retry(ticker_jk: str, max_attempts: int = 3) -> dict:
    for attempt in range(max_attempts):
        try:
            info = get_info(ticker_jk)
            if info and len(info) > 5:
                return info
        except YFinanceUnavailable:
            raise
        except Exception as exc:
            if attempt == max_attempts - 1:
                raise exc
        time.sleep(2 ** attempt)
    return {}


def _sectors_get(endpoint: str, params: dict | None = None) -> Optional[dict]:
    if not SECTORS_API_KEY:
        return None
    try:
        url = f"{SECTORS_BASE}/{endpoint.lstrip('/')}"
        resp = _sess.get(url, params=params or {}, timeout=12)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def get_sectors_stock(ticker: str) -> Optional[dict]:
    return _sectors_get(f"stock/{ticker}/")


def get_sectors_financials(ticker: str) -> Optional[dict]:
    return _sectors_get(f"financials/{ticker}/")


def get_sectors_flow(ticker: str) -> Optional[dict]:
    return _sectors_get(f"stock/{ticker}/foreign-flow/")


def _fetch_history(stock) -> pd.DataFrame:
    hist = pd.DataFrame()
    symbol = getattr(stock, "ticker", None)
    for kwargs in [
        {"period": "1y", "interval": "1d", "auto_adjust": True},
        {"period": "2y", "interval": "1d", "auto_adjust": True},
        {"period": "6mo", "interval": "1d", "auto_adjust": True},
    ]:
        try:
            raw = get_history(symbol, **kwargs) if symbol else stock.history(**kwargs)
            if not raw.empty and len(raw) >= 20:
                hist = raw
                break
        except YFinanceUnavailable:
            raise
        except Exception:
            continue

    if hist.empty:
        return pd.DataFrame()

    hist.columns = [c.strip() for c in hist.columns]
    hist = hist.rename(columns={c: c.title() for c in hist.columns})
    ohlcv = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in hist.columns]
    return hist[ohlcv].dropna(subset=ohlcv)


def _rsi(close: pd.Series, period=14) -> float:
    try:
        delta = close.diff().dropna()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        if not gain.empty and not loss.empty:
            last_gain = float(gain.iloc[-1]) if not np.isnan(gain.iloc[-1]) else 0.0
            last_loss = float(loss.iloc[-1]) if not np.isnan(loss.iloc[-1]) else 0.0
            if abs(last_gain) < 1e-10 and abs(last_loss) < 1e-10:
                return 50.0
        rs = gain / loss.replace(0, 1e-10)
        val = (100 - 100 / (1 + rs)).iloc[-1]
        return round(float(val), 2) if not np.isnan(val) else 50.0
    except Exception:
        return 50.0


def _rsi_series(close: pd.Series, period=14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def _stoch_rsi(close: pd.Series, rsi_period=14, stoch_period=14, k=3, d=3):
    try:
        rsi = _rsi_series(close, rsi_period)
        lo = rsi.rolling(stoch_period).min()
        hi = rsi.rolling(stoch_period).max()
        stoch = (rsi - lo) / (hi - lo + 1e-10) * 100
        k_val = stoch.rolling(k).mean()
        d_val = k_val.rolling(d).mean()
        kv = round(float(k_val.iloc[-1]), 2) if not np.isnan(k_val.iloc[-1]) else 50.0
        dv = round(float(d_val.iloc[-1]), 2) if not np.isnan(d_val.iloc[-1]) else 50.0
        return kv, dv
    except Exception:
        return 50.0, 50.0


def _williams_r(high: pd.Series, low: pd.Series, close: pd.Series, period=14) -> float:
    try:
        hh = high.rolling(period).max()
        ll = low.rolling(period).min()
        val = (hh - close) / (hh - ll + 1e-10) * -100
        v = float(val.iloc[-1])
        return round(v, 2) if not np.isnan(v) else -50.0
    except Exception:
        return -50.0


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period=14) -> tuple:
    try:
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)

        up = high.diff()
        down = (-low.diff())
        plus_dm = pd.Series(np.where((up > down) & (up > 0), up.values, 0.0), index=high.index)
        minus_dm = pd.Series(np.where((down > up) & (down > 0), down.values, 0.0), index=low.index)

        alpha = 1.0 / period
        atr_w = tr.ewm(alpha=alpha, adjust=False).mean()
        plus_di = 100.0 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / (atr_w + 1e-10)
        minus_di = 100.0 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / (atr_w + 1e-10)
        dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
        adx_val = dx.ewm(alpha=alpha, adjust=False).mean()

        adx_v = float(adx_val.iloc[-1])
        pdi_v = float(plus_di.iloc[-1])
        mdi_v = float(minus_di.iloc[-1])
        if np.isnan(adx_v) or adx_v < 0 or adx_v > 100:
            return (0.0, 0.0, 0.0)
        return (round(adx_v, 2), round(pdi_v, 2), round(mdi_v, 2))
    except Exception:
        return (0.0, 0.0, 0.0)


def _macd(close: pd.Series):
    try:
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        line = ema12 - ema26
        sig = line.ewm(span=9, adjust=False).mean()
        hist = line - sig
        return (round(float(line.iloc[-1]), 4), round(float(sig.iloc[-1]), 4), round(float(hist.iloc[-1]), 4))
    except Exception:
        return (0.0, 0.0, 0.0)


def _atr(high, low, close, period=14) -> float:
    try:
        prev = close.shift(1)
        tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
        val = tr.rolling(period).mean().iloc[-1]
        return round(float(val), 2) if not np.isnan(val) else 0.0
    except Exception:
        return 0.0


def _obv(close: pd.Series, vol: pd.Series) -> dict:
    try:
        direction = np.sign(close.diff().fillna(0))
        obv_series = (direction * vol).cumsum()
        obv_now = float(obv_series.iloc[-1])
        obv_ma20 = float(obv_series.rolling(20).mean().iloc[-1])
        obv_trend = "Akumulasi" if obv_now > obv_ma20 else "Distribusi"
        return {
            "value": round(obv_now / 1e6, 2),
            "ma20": round(obv_ma20 / 1e6, 2),
            "trend": obv_trend,
            "signal": "🟢 Akumulasi" if obv_trend == "Akumulasi" else "🔴 Distribusi",
        }
    except Exception:
        return {"value": 0.0, "ma20": 0.0, "trend": "N/A", "signal": "N/A"}


def _vwap(high: pd.Series, low: pd.Series, close: pd.Series, vol: pd.Series) -> float:
    try:
        typical = (high + low + close) / 3
        vwap = (typical * vol).rolling(20).sum() / vol.rolling(20).sum()
        val = float(vwap.iloc[-1])
        return round(val, 2) if not np.isnan(val) else 0.0
    except Exception:
        return 0.0


def _pivot_points(high: pd.Series, low: pd.Series, close: pd.Series) -> dict:
    try:
        ph = float(high.iloc[-2])
        pl = float(low.iloc[-2])
        pc = float(close.iloc[-2])
        pp = (ph + pl + pc) / 3
        r1 = 2 * pp - pl
        r2 = pp + (ph - pl)
        r3 = ph + 2 * (pp - pl)
        s1 = 2 * pp - ph
        s2 = pp - (ph - pl)
        s3 = pl - 2 * (ph - pp)
        return {
            "pp": round(pp, 0), "r1": round(r1, 0), "r2": round(r2, 0), "r3": round(r3, 0),
            "s1": round(s1, 0), "s2": round(s2, 0), "s3": round(s3, 0),
        }
    except Exception:
        return {"pp": 0, "r1": 0, "r2": 0, "r3": 0, "s1": 0, "s2": 0, "s3": 0}


def _fibonacci(high: pd.Series, low: pd.Series, lookback: int = 50) -> dict:
    try:
        recent_high = float(high.iloc[-lookback:].max())
        recent_low = float(low.iloc[-lookback:].min())
        diff = recent_high - recent_low
        return {
            "high": round(recent_high, 0),
            "low": round(recent_low, 0),
            "r_236": round(recent_high - diff * 0.236, 0),
            "r_382": round(recent_high - diff * 0.382, 0),
            "r_500": round(recent_high - diff * 0.500, 0),
            "r_618": round(recent_high - diff * 0.618, 0),
            "r_786": round(recent_high - diff * 0.786, 0),
        }
    except Exception:
        return {"high": 0, "low": 0, "r_236": 0, "r_382": 0, "r_500": 0, "r_618": 0, "r_786": 0}


def _swing_sr(high: pd.Series, low: pd.Series, window: int = 5) -> dict:
    try:
        swing_highs = []
        swing_lows = []
        h = high.values
        l = low.values
        for i in range(window, len(h) - window):
            if all(h[i] >= h[i-j] for j in range(1, window + 1)) and all(h[i] >= h[i+j] for j in range(1, window + 1)):
                swing_highs.append(h[i])
            if all(l[i] <= l[i-j] for j in range(1, window + 1)) and all(l[i] <= l[i+j] for j in range(1, window + 1)):
                swing_lows.append(l[i])

        current = float(high.iloc[-1])
        res_levels = sorted([x for x in swing_highs if x > current * 0.98], reverse=False)[:3]
        sup_levels = sorted([x for x in swing_lows if x < current * 1.02], reverse=True)[:3]
        return {"resistances": [round(x, 0) for x in res_levels], "supports": [round(x, 0) for x in sup_levels]}
    except Exception:
        return {"resistances": [], "supports": []}


def _candlestick_pattern(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> list:
    patterns = []
    try:
        o = open_.values
        h = high.values
        l = low.values
        c = close.values
        n = len(c)
        if n < 3:
            return patterns

        body = abs(c[-1] - o[-1])
        full_rng = h[-1] - l[-1]
        upper_sh = h[-1] - max(c[-1], o[-1])
        lower_sh = min(c[-1], o[-1]) - l[-1]

        if full_rng > 0 and body / full_rng < 0.1:
            patterns.append("Doji ⚖️")
        if lower_sh > 2 * body and upper_sh < body * 0.5 and c[-1] > o[-1]:
            patterns.append("Hammer 🔨 (Bullish Reversal)")
        if upper_sh > 2 * body and lower_sh < body * 0.5 and c[-1] < o[-1]:
            patterns.append("Shooting Star ⭐ (Bearish Reversal)")
        if c[-2] < o[-2] and c[-1] > o[-1] and c[-1] > o[-2] and o[-1] < c[-2]:
            patterns.append("Bullish Engulfing 🟢 (Kuat)")
        if c[-2] > o[-2] and c[-1] < o[-1] and c[-1] < o[-2] and o[-1] > c[-2]:
            patterns.append("Bearish Engulfing 🔴 (Kuat)")
    except Exception:
        pass
    return patterns if patterns else ["Tidak ada pola signifikan"]


def _calc_technical(hist: pd.DataFrame) -> dict:
    empty = {
        "last_close": 0.0, "ma20": 0.0, "ma50": 0.0, "ma200": 0.0,
        "ema9": 0.0, "rsi": 50.0, "rsi_label": "Neutral",
        "stoch_rsi_k": 50.0, "stoch_rsi_d": 50.0, "stoch_rsi_label": "Neutral",
        "williams_r": -50.0, "williams_r_label": "Neutral",
        "adx": 0.0, "adx_plus_di": 0.0, "adx_minus_di": 0.0, "adx_label": "Sideways",
        "macd": 0.0, "macd_signal": 0.0, "macd_histogram": 0.0, "macd_cross": "N/A",
        "atr": 0.0, "support": 0.0, "resistance": 0.0,
        "bb_upper": 0.0, "bb_lower": 0.0, "bb_pct": 0.0,
        "volume_breakout": False, "vol_ratio": 0.0,
        "obv": {}, "vwap": 0.0, "pivot": {}, "fibonacci": {}, "swing_sr": {},
        "candlestick_patterns": [], "trend": "N/A", "trend_strength": "Lemah",
        "ma200_valid": False, "data_rows": 0, "atr_sl": 0.0, "atr_tp1": 0.0, "atr_tp2": 0.0, "atr_rr": 0.0,
        "aggressive_sl": 0.0, "aggressive_tp1": 0.0, "aggressive_tp2": 0.0,
        "momentum_cond_count": 0, "five_cond_count": 0,
        "pullback_ready": False, "breakout_ready": False,
        "entry_signal_count": 0, "aggressive_signal_count": 0,
        "entry_modes_ready": [], "preferred_entry_mode": "WAIT",
        "near_ma20": False, "near_vwap": False, "bounce": False,
        "adaptive_hold_days": 12,
    }
    if hist is None or hist.empty or len(hist) < 20:
        return empty

    open_ = hist["Open"].astype(float) if "Open" in hist.columns else hist["Close"].astype(float)
    close = hist["Close"].astype(float)
    high = hist["High"].astype(float)
    low = hist["Low"].astype(float)
    vol = hist["Volume"].astype(float)
    n = len(hist)

    lc = round(float(close.iloc[-1]), 2)
    ema9 = round(float(close.ewm(span=9, adjust=False).mean().iloc[-1]), 2)
    ma20 = round(float(close.rolling(20).mean().iloc[-1]), 2) if n >= 20 else lc
    ma50 = round(float(close.rolling(50).mean().iloc[-1]), 2) if n >= 50 else ma20
    ma200_raw = close.rolling(200).mean().iloc[-1] if n >= 200 else float("nan")
    ma200 = round(float(ma200_raw), 2) if not np.isnan(ma200_raw) else 0.0

    ref_ma = ma50 if n >= 50 else ma20
    trend = "Bullish" if lc > ref_ma else "Bearish"
    if ma200 > 0 and lc > ma50 > ma20 and lc > ma200:
        strength = "Kuat"
    elif lc > ref_ma:
        strength = "Sedang"
    else:
        strength = "Lemah"

    rsi_val = _rsi(close)
    if rsi_val >= 70:
        rsi_lbl = "Overbought ⚠️"
    elif rsi_val >= 65:
        rsi_lbl = "Approaching OB"
    elif rsi_val <= 30:
        rsi_lbl = "Oversold 🟢"
    elif rsi_val <= 35:
        rsi_lbl = "Near Oversold"
    else:
        rsi_lbl = "Neutral"

    srsi_k, srsi_d = _stoch_rsi(close)
    srsi_lbl = "Overbought" if srsi_k >= 80 else ("Oversold" if srsi_k <= 20 else "Neutral")
    wr_val = _williams_r(high, low, close)
    wr_lbl = "Overbought" if wr_val >= -20 else ("Oversold" if wr_val <= -80 else "Neutral")
    adx_val, plus_di, minus_di = _adx(high, low, close)
    adx_lbl = "Trend Kuat" if adx_val >= 25 else ("Trend Lemah" if adx_val >= 15 else "Sideways")
    ml, ms, mh = _macd(close)
    atr_val = _atr(high, low, close)

    atr_sl = round(lc - 1.5 * atr_val, 0) if atr_val > 0 else 0.0
    atr_tp1 = round(lc + 2.0 * atr_val, 0) if atr_val > 0 else 0.0
    atr_tp2 = round(lc + 3.0 * atr_val, 0) if atr_val > 0 else 0.0
    risk = lc - atr_sl
    reward = atr_tp1 - lc
    atr_rr = round(reward / risk, 2) if risk > 0 else 0.0

    bb_mid = float(close.rolling(20).mean().iloc[-1]) if n >= 20 else lc
    bb_std = float(close.rolling(20).std().iloc[-1]) if n >= 20 else 0
    bb_up = round(bb_mid + 2 * bb_std, 2)
    bb_lo = round(bb_mid - 2 * bb_std, 2)
    bb_pct = round((lc - bb_lo) / (bb_up - bb_lo + 1e-10) * 100, 1)

    sup_roll = round(float(low.rolling(20).min().iloc[-1]), 2)
    res_roll = round(float(high.rolling(20).max().iloc[-1]), 2)
    avg_vol = float(vol.rolling(20).mean().iloc[-1]) if n >= 20 else float(vol.mean())
    last_vol = float(vol.iloc[-1])
    vol_ratio = round(last_vol / avg_vol, 2) if avg_vol > 0 else 0.0

    obv_data = _obv(close, vol)
    vwap_val = _vwap(high, low, close, vol)
    vwap_signal = "Di atas VWAP 🟢" if lc > vwap_val > 0 else "Di bawah VWAP 🔴"
    pivot = _pivot_points(high, low, close)
    fib = _fibonacci(high, low, lookback=min(50, n))
    swing = _swing_sr(high, low)
    candles = _candlestick_pattern(open_, high, low, close)

    prev_close = float(close.iloc[-2]) if n > 1 else lc
    near_ma20 = ma20 > 0 and abs(lc - ma20) / ma20 < 0.015
    near_vwap = vwap_val > 0 and abs(lc - vwap_val) / vwap_val < 0.012
    bounce = lc > prev_close
    high_20 = float(high.iloc[-21:-1].max()) if n >= 21 else float(high.iloc[:-1].max()) if n > 1 else 0.0

    cond1_trend = adx_val > max(MIN_ADX_ENTRY, 18.0) and plus_di > minus_di
    cond2_rsi = 38 <= rsi_val <= 68
    cond3_vb = bool(last_vol > avg_vol * max(MIN_VOL_RATIO_ENTRY, 1.2)) if avg_vol > 0 else False
    cond4_ma20 = lc > ma50 if ma50 > 0 else lc > ma20
    cond5_srsi = srsi_k > srsi_d
    momentum_cond_count = sum([cond1_trend, cond2_rsi, cond3_vb, cond4_ma20, cond5_srsi])
    pullback_ready = (
        (near_ma20 or near_vwap)
        and 35 <= rsi_val <= 55
        and ((vol_ratio >= 0.9) if avg_vol > 0 else False)
        and (lc > ma50 if ma50 > 0 else True)
        and bounce
    )
    breakout_ready = (
        vol_ratio >= 1.8
        and high_20 > 0
        and lc > high_20 * 0.995
        and rsi_val < 75
        and adx_val > MIN_ADX_ENTRY
        and (ma200 == 0.0 or lc > ma200 * 0.97)
    )
    entry_modes_ready = []
    if momentum_cond_count >= MIN_ENTRY_CONDITIONS and ml > ms:
        entry_modes_ready.append("MOMENTUM")
    if pullback_ready:
        entry_modes_ready.append("PULLBACK")
    if breakout_ready:
        entry_modes_ready.append("BREAKOUT")

    if breakout_ready:
        preferred_entry_mode = "BREAKOUT"
    elif pullback_ready:
        preferred_entry_mode = "PULLBACK"
    elif momentum_cond_count >= MIN_ENTRY_CONDITIONS and ml > ms:
        preferred_entry_mode = "MOMENTUM"
    else:
        preferred_entry_mode = "WAIT"

    aggressive_signal_count = len(entry_modes_ready)

    return {
        "last_close": lc, "ema9": ema9, "ma20": ma20, "ma50": ma50, "ma200": ma200,
        "ma200_valid": ma200 > 0, "rsi": rsi_val, "rsi_label": rsi_lbl,
        "stoch_rsi_k": srsi_k, "stoch_rsi_d": srsi_d, "stoch_rsi_label": srsi_lbl,
        "williams_r": wr_val, "williams_r_label": wr_lbl,
        "adx": adx_val, "adx_plus_di": plus_di, "adx_minus_di": minus_di, "adx_label": adx_lbl,
        "macd": ml, "macd_signal": ms, "macd_histogram": mh,
        "macd_cross": "Bullish Cross" if ml > ms else "Bearish Cross",
        "atr": atr_val, "atr_sl": atr_sl, "atr_tp1": atr_tp1, "atr_tp2": atr_tp2, "atr_rr": atr_rr,
        "aggressive_sl": atr_sl, "aggressive_tp1": atr_tp1, "aggressive_tp2": atr_tp2,
        "support": sup_roll, "resistance": res_roll, "bb_upper": bb_up, "bb_lower": bb_lo, "bb_pct": bb_pct,
        "volume_breakout": bool(last_vol > avg_vol * 1.8), "vol_ratio": vol_ratio,
        "obv": obv_data, "vwap": vwap_val, "vwap_signal": vwap_signal,
        "pivot": pivot, "fibonacci": fib, "swing_sr": swing, "candlestick_patterns": candles,
        "trend": trend, "trend_strength": strength, "data_rows": n,
        "five_cond_count": momentum_cond_count,
        "momentum_cond_count": momentum_cond_count,
        "pullback_ready": pullback_ready,
        "breakout_ready": breakout_ready,
        "entry_signal_count": aggressive_signal_count,
        "aggressive_signal_count": aggressive_signal_count,
        "entry_modes_ready": entry_modes_ready,
        "preferred_entry_mode": preferred_entry_mode,
        "near_ma20": near_ma20,
        "near_vwap": near_vwap,
        "bounce": bounce,
        "adaptive_hold_days": 12,
    }


def _get_yfinance_data(ticker: str) -> dict:
    try:
        ticker_jk = f"{ticker}.JK"
        info = _yf_info_with_retry(ticker_jk)
        price = _safe(info.get("currentPrice") or info.get("regularMarketPrice") or info.get("ask"))
        prev = _safe(info.get("previousClose"))
        change = round((price - prev) / prev * 100, 2) if prev > 0 else 0.0
        vol = _safe(info.get("volume") or info.get("regularMarketVolume"))
        avg_vol = _safe(info.get("averageVolume") or info.get("averageDailyVolume10Day"))
        mktcap = _safe(info.get("marketCap"))
        daily_val = price * vol

        market = {
            "price": round(price, 2),
            "prev_close": round(prev, 2),
            "change_pct": change,
            "day_high": round(_safe(info.get("dayHigh") or info.get("regularMarketDayHigh")), 2),
            "day_low": round(_safe(info.get("dayLow") or info.get("regularMarketDayLow")), 2),
            "volume": int(vol),
            "avg_volume": int(avg_vol),
            "vol_ratio": round(vol / avg_vol, 2) if avg_vol > 0 else 0.0,
            "market_cap": _fmt_idr(mktcap),
            "market_cap_raw": mktcap,
            "value_raw": daily_val,
            "daily_value": _fmt_idr(daily_val),
        }

        roe_raw = _safe(info.get("returnOnEquity"))
        der_raw = _safe(info.get("debtToEquity"))
        sector_label = get_sector_context(ticker, info.get("sector", "")).get("label", "")
        der_normalized = normalize_der(der_raw, sector_label)
        fundamental = {
            "per": round(_safe(info.get("trailingPE")), 2),
            "pbv": round(_safe(info.get("priceToBook")), 2),
            "eps": round(_safe(info.get("trailingEps")), 4),
            "roe": round(roe_raw * 100, 2),
            "der": round(der_normalized, 2),
            "der_raw": round(der_raw, 2),
            "revenue": _fmt_idr(_safe(info.get("totalRevenue"))),
            "net_profit": _fmt_idr(_safe(info.get("netIncomeToCommon"))),
            "revenue_growth": round(_safe(info.get("revenueGrowth")) * 100, 2),
            "eps_growth": round(_safe(info.get("earningsGrowth")) * 100, 2),
            "cash_flow": _fmt_idr(_safe(info.get("operatingCashflow"))),
            "market_cap_raw": mktcap,
            "dividend_yield": round(_safe(info.get("dividendYield")) * 100, 2),
        }

        stock = type("TickerRef", (), {"ticker": ticker_jk})()
        hist = _fetch_history(stock)
        technical = _calc_technical(hist)
        return {
            "ticker": ticker,
            "company_name": info.get("longName") or info.get("shortName") or ticker,
            "sector": info.get("sector", "N/A"),
            "industry": info.get("industry", "N/A"),
            "market": market,
            "fundamental": fundamental,
            "technical": technical,
        }
    except YFinanceUnavailable as exc:
        return {"error": f"Provider market data sementara tidak tersedia: {exc}"}
    except Exception as exc:
        return {"error": f"Gagal fetch {ticker}: {str(exc)}"}


def _merge_fundamental(yf_data, sectors_data, sectors_fin):
    base = yf_data.get("fundamental", {})
    sector_label = get_sector_context(yf_data.get("ticker", ""), yf_data.get("sector", "")).get("label", "")
    if not sectors_data and not sectors_fin:
        return base
    if sectors_data:
        base["per"] = round(_safe(sectors_data.get("pe_ratio", sectors_data.get("per", base["per"]))), 2)
        pbv_raw = _safe(sectors_data.get("pb_ratio", sectors_data.get("pbv", base.get("pbv", 0))))
        if 0 < pbv_raw <= 100:
            base["pbv"] = round(pbv_raw, 2)
        base["roe"] = round(_safe(sectors_data.get("roe", base["roe"])), 2)
        sectors_der = _safe(sectors_data.get("debt_to_equity", base["der"]))
        base["der"] = normalize_der(sectors_der, sector_label)
    if sectors_fin:
        base["revenue_growth"] = round(_safe(sectors_fin.get("revenue_growth", base["revenue_growth"])), 2)
        base["eps_growth"] = round(_safe(sectors_fin.get("eps_growth", base["eps_growth"])), 2)
    return base


def _get_flow(ticker: str) -> dict:
    sf = get_sectors_flow(ticker)
    if sf:
        fb = _safe(sf.get("foreign_buy", sf.get("foreignBuy", 0)))
        fs = _safe(sf.get("foreign_sell", sf.get("foreignSell", 0)))
        fn = _safe(sf.get("foreign_net", sf.get("foreignNet", fb - fs)))
        if fb > 0 or fs > 0 or fn != 0:
            return {
                "foreign_buy": _fmt_lot(fb),
                "foreign_sell": _fmt_lot(fs),
                "net_foreign": _fmt_lot(fn),
                "net_raw": fn,
                "signal": "🟢 Net Buy" if fn > 0 else ("🔴 Net Sell" if fn < 0 else "⚪ Neutral"),
                "source": "sectors.app",
            }

    idx_endpoints = [
        ("https://idx.co.id/umbraco/Surface/StockData/GetTradingSummary", {"code": ticker, "lang": "id"}),
        (f"https://idx.co.id/api/stock-summary?code={ticker}", {}),
    ]
    for url, params in idx_endpoints:
        try:
            resp = _sess.get(url, params=params, timeout=8)
            if resp.status_code == 200:
                text = resp.text.strip()
                if text and text not in ("null", "[]", "{}", ""):
                    data = resp.json() or {}
                    fb = _safe(data.get("ForeignBuy") or data.get("foreignBuy") or data.get("foreign_buy") or 0)
                    fs = _safe(data.get("ForeignSell") or data.get("foreignSell") or data.get("foreign_sell") or 0)
                    fn = _safe(data.get("ForeignNet") or data.get("foreignNet") or data.get("foreign_net") or (fb - fs))
                    if fb > 0 or fs > 0:
                        return {
                            "foreign_buy": _fmt_lot(fb),
                            "foreign_sell": _fmt_lot(fs),
                            "net_foreign": _fmt_lot(fn),
                            "net_raw": fn,
                            "signal": "🟢 Net Buy" if fn > 0 else ("🔴 Net Sell" if fn < 0 else "⚪ Neutral"),
                            "source": "IDX Official",
                        }
        except Exception:
            pass

    try:
        inst = get_institutional_holders(f"{ticker}.JK")
        if inst is not None and not inst.empty:
            pct_held = float(inst["% Out"].iloc[0]) if "% Out" in inst.columns else 0.0
            if pct_held > 0.35:
                proxy_signal = "🟢 Net Buy (proxy: inst >35%)"
                proxy_net = 10000.0
            elif pct_held > 0.15:
                proxy_signal = "⚪ Neutral (proxy: inst 15-35%)"
                proxy_net = 0.0
            else:
                proxy_signal = "🔴 Net Sell (proxy: inst <15%)"
                proxy_net = -10000.0
            return {
                "foreign_buy": "N/A",
                "foreign_sell": "N/A",
                "net_foreign": f"proxy ~{pct_held*100:.0f}% inst",
                "net_raw": proxy_net,
                "signal": proxy_signal,
                "source": "yfinance proxy",
            }
    except Exception:
        pass

    return {
        "foreign_buy": "N/A",
        "foreign_sell": "N/A",
        "net_foreign": "N/A",
        "net_raw": 0.0,
        "signal": "⚪ Data tidak tersedia",
        "source": "unavailable",
    }


def get_stock_data(ticker: str) -> dict:
    try:
        ticker = normalize_ticker(ticker)
    except ValueError as exc:
        return {"error": str(exc)}
    cached = _stock_data_cache.get(("stock_data", ticker))
    if cached is not None:
        return cached

    sectors_data = get_sectors_stock(ticker)
    sectors_fin = get_sectors_financials(ticker) if sectors_data else None
    yf_data = _get_yfinance_data(ticker)
    if "error" in yf_data:
        return yf_data

    fundamental = _merge_fundamental(yf_data, sectors_data, sectors_fin)
    if _safe(fundamental.get("pbv", 0)) > 100:
        if sectors_data:
            pbv_sectors = _safe(sectors_data.get("pb_ratio", sectors_data.get("pbv", 0)))
            fundamental["pbv"] = round(pbv_sectors, 2) if 0 < pbv_sectors <= 100 else 0.0
            if 0 < pbv_sectors <= 100:
                fundamental["pbv_source"] = "sectors.app"
        else:
            fundamental["pbv"] = 0.0

    market = yf_data["market"]
    technical = yf_data["technical"]
    flow = _get_flow(ticker)
    sector_ctx = get_sector_context(ticker, yf_data.get("sector", ""))
    coal_data = get_coal_price() if sector_ctx["label"] == "Coal Mining" else {}

    mktcap_t = _safe(fundamental.get("market_cap_raw", 0)) / 1e12
    daily_b = _safe(market.get("value_raw", 0)) / 1e9
    liquid = (mktcap_t >= MIN_MARKET_CAP_T) and (daily_b >= MIN_DAILY_VALUE_B)

    avg_vol = _safe(market.get("avg_volume", 1))
    last_vol = _safe(market.get("volume", 0))
    market["vol_ratio"] = round(last_vol / avg_vol, 2) if avg_vol > 0 else 0.0

    score = compute_score(fundamental, technical, flow, sector_ctx, coal_data)
    result = {
        "ticker": ticker,
        "company_name": yf_data.get("company_name", ticker),
        "sector": yf_data.get("sector", "N/A"),
        "industry": yf_data.get("industry", "N/A"),
        "sector_context": sector_ctx,
        "coal_data": coal_data,
        "liquid": liquid,
        "market_cap_t": round(mktcap_t, 2),
        "daily_value_b": round(daily_b, 2),
        "market": market,
        "fundamental": fundamental,
        "technical": technical,
        "flow": flow,
        "score": score,
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M WIB"),
    }
    return _stock_data_cache.set(("stock_data", ticker), result, STOCK_DATA_TTL_SECONDS)
