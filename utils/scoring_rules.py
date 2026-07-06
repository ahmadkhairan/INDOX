from __future__ import annotations

from config import SCORE_WEIGHTS
from utils.fundamental_utils import normalize_der


def safe_number(val, default=0.0):
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def compute_score(
    fund: dict,
    tech: dict,
    flow: dict,
    sector_ctx: dict | None = None,
    coal_data: dict | None = None,
) -> dict:
    if sector_ctx is None:
        sector_ctx = {
            "label": "General",
            "roe_ok_min": 15.0,
            "per_ok_max": 15.0,
            "der_ok_max": 1.0,
            "flow_weight_bonus": 0.0,
        }

    roe_min = sector_ctx.get("roe_ok_min", 15.0)
    per_max = sector_ctx.get("per_ok_max", 15.0)
    der_max = sector_ctx.get("der_ok_max", 1.0)
    flow_bonus_w = sector_ctx.get("flow_weight_bonus", 0.0)

    f_score = 0.0
    roe = safe_number(fund.get("roe", 0))
    per = safe_number(fund.get("per", 99))
    pbv = safe_number(fund.get("pbv", 99))
    der = normalize_der(fund.get("der", 99), sector_ctx.get("label", ""))
    rev_g = safe_number(fund.get("revenue_growth", 0))
    eps_g = safe_number(fund.get("eps_growth", 0))
    div_y = safe_number(fund.get("dividend_yield", 0))

    if roe >= roe_min:
        f_score += 20
    elif roe > 0:
        f_score += 10
    if 0 < per <= per_max:
        f_score += 20
    elif 0 < per <= per_max * 1.7:
        f_score += 10
    if 0 < pbv <= 2.0:
        f_score += 20
    elif 0 < pbv <= 3.0:
        f_score += 10
    if der <= der_max:
        f_score += 20
    elif der <= der_max * 2:
        f_score += 10
    if rev_g >= 10.0:
        f_score += 10
    if eps_g >= 10.0:
        f_score += 10
    if div_y >= 5.0:
        f_score = min(f_score + 5, 100)

    t_score = 0.0
    trend = tech.get("trend", "Bearish")
    rsi = safe_number(tech.get("rsi", 50))
    srsi_k = safe_number(tech.get("stoch_rsi_k", 50))
    srsi_d = safe_number(tech.get("stoch_rsi_d", 50))
    wr = safe_number(tech.get("williams_r", -50))
    adx = safe_number(tech.get("adx", 0))
    plus_di = safe_number(tech.get("adx_plus_di", 0))
    minus_di = safe_number(tech.get("adx_minus_di", 0))
    macd_h = safe_number(tech.get("macd_histogram", 0))
    vb = tech.get("volume_breakout", False)
    vr = safe_number(tech.get("vol_ratio", 1))
    lc = safe_number(tech.get("last_close", 0))
    ma200 = safe_number(tech.get("ma200", 0))
    ma200_valid = tech.get("ma200_valid", False)
    vwap = safe_number(tech.get("vwap", 0))
    obv_trend = tech.get("obv", {}).get("trend", "")
    five_cond = int(tech.get("five_cond_count", 0))
    momentum_cond = int(tech.get("momentum_cond_count", five_cond))
    pullback_ready = bool(tech.get("pullback_ready", False))
    breakout_ready = bool(tech.get("breakout_ready", False))
    signal_count = int(tech.get("aggressive_signal_count", tech.get("entry_signal_count", 0)))
    preferred_mode = str(tech.get("preferred_entry_mode", "WAIT")).upper()

    if trend == "Bullish":
        t_score += 15
    if 38 <= rsi <= 68:
        t_score += 10
    elif 35 <= rsi <= 55:
        t_score += 12
    elif rsi < 30:
        t_score += 7
    if srsi_k > srsi_d and srsi_k < 85:
        t_score += 10
    elif srsi_k < 20:
        t_score += 5
    if -60 <= wr <= -20:
        t_score += 10
    elif wr < -80:
        t_score += 6
    if adx >= 25 and plus_di > minus_di:
        t_score += 10
    elif adx >= 18 and plus_di > minus_di:
        t_score += 5
    if macd_h > 0:
        t_score += 15
    elif macd_h > -0.001:
        t_score += 5
    if vb:
        t_score += 10
    elif vr > 1.1:
        t_score += 5
    if obv_trend == "Akumulasi":
        t_score += 10
    if vwap > 0 and lc > vwap:
        t_score += 5
    if ma200_valid and ma200 > 0 and lc > ma200:
        t_score += 5
    if preferred_mode == "BREAKOUT":
        t_score = min(t_score + 12, 100)
    elif preferred_mode == "PULLBACK":
        t_score = min(t_score + 10, 100)
    elif preferred_mode == "MOMENTUM":
        t_score = min(t_score + 8, 100)
    if signal_count >= 2:
        t_score = min(t_score + 10, 100)
    elif signal_count >= 1:
        t_score = min(t_score + 5, 100)
    if momentum_cond >= 4:
        t_score = min(t_score + 6, 100)
    elif momentum_cond >= 3:
        t_score = min(t_score + 3, 100)
    t_score = min(t_score, 100.0)

    fn = safe_number(flow.get("net_raw", 0))
    normalized = fn / 100_000
    fl_score = max(0.0, min(100.0, 50.0 + normalized * 50.0))

    base_w = SCORE_WEIGHTS.copy()
    if flow_bonus_w > 0:
        base_w = {
            "fundamental": base_w["fundamental"] - flow_bonus_w / 2,
            "technical": base_w["technical"],
            "flow": base_w["flow"] + flow_bonus_w,
            "sentiment": base_w.get("sentiment", 0.10) - flow_bonus_w / 2,
        }

    total = (
        f_score * base_w["fundamental"] +
        t_score * base_w["technical"] +
        fl_score * base_w.get("flow", 0.20)
    )
    if coal_data and coal_data.get("rally", False):
        total = min(total + coal_data.get("score_bonus", 0), 100.0)

    if (signal_count >= 2 or (breakout_ready and momentum_cond >= 3)) and obv_trend == "Akumulasi" and fl_score > 60:
        confidence = "High"
    elif (signal_count >= 1 or momentum_cond >= 3 or pullback_ready) and t_score >= 50:
        confidence = "Medium"
    else:
        confidence = "Low"

    base_wp = 44.0
    base_wp += momentum_cond * 3.5
    base_wp += signal_count * 5.0
    base_wp += 4.0 if breakout_ready else 0.0
    base_wp += 3.0 if pullback_ready else 0.0
    base_wp += 5.0 if obv_trend == "Akumulasi" else 0.0
    base_wp += 5.0 if fl_score > 60 else 0.0
    base_wp += 3.0 if adx >= 25 else 0.0
    base_wp = min(base_wp, 82.0)
    grade = "A" if total >= 75 else ("B" if total >= 55 else ("C" if total >= 35 else "D"))
    if signal_count >= 2 or (breakout_ready and momentum_cond >= 3):
        entry_quality = "CLEAN"
    elif signal_count >= 1 or momentum_cond >= 3:
        entry_quality = "GOOD"
    else:
        entry_quality = "WEAK"

    return {
        "total": round(total, 1),
        "fundamental": round(f_score, 1),
        "technical": round(t_score, 1),
        "flow": round(fl_score, 1),
        "grade": grade,
        "confidence": confidence,
        "win_probability": round(base_wp, 1),
        "five_cond_count": five_cond,
        "momentum_cond_count": momentum_cond,
        "aggressive_signal_count": signal_count,
        "preferred_entry_mode": preferred_mode,
        "entry_quality": entry_quality,
    }
