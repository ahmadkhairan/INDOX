from __future__ import annotations

import re
from typing import Iterable

from utils.position_sizer import adaptive_risk_pct, kelly_aggressive


RETAIL_POSITION_CAP = {
    "BULL": 0.15,
    "CAUTION": 0.08,
    "BEAR": 0.03,
    "UNKNOWN": 0.06,
}

QUANT_WEIGHTS = {
    "base_score": 0.30,
    "technical": 0.18,
    "flow": 0.12,
    "rr": 0.10,
    "volume": 0.08,
    "adx": 0.06,
    "rsi": 0.04,
    "liquidity": 0.04,
    "signals": 0.08,
}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _safe_num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _norm_ratio(value: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    return _clamp((value - lo) / (hi - lo), 0.0, 1.0) * 100.0


def _rsi_score(rsi: float) -> float:
    if 42.0 <= rsi <= 63.0:
        return 100.0
    if 38.0 <= rsi < 42.0 or 63.0 < rsi <= 67.0:
        return 70.0
    if 35.0 <= rsi < 38.0 or 67.0 < rsi <= 72.0:
        return 40.0
    return 10.0


def _trend_bonus(candidate: dict) -> float:
    tech = candidate.get("technical", {})
    trend = str(tech.get("trend", "")).upper()
    macd_cross = str(tech.get("macd_cross", "")).upper()
    plus_di = _safe_num(tech.get("adx_plus_di", 0))
    minus_di = _safe_num(tech.get("adx_minus_di", 0))
    bonus = 0.0
    if "BULL" in trend or "UP" in trend:
        bonus += 6.0
    if "BULL" in macd_cross or "GOLDEN" in macd_cross:
        bonus += 4.0
    if plus_di > minus_di:
        bonus += 3.0
    return bonus


def estimate_position_size(candidate: dict, regime: dict | None = None) -> dict:
    score = candidate.get("score", {})
    tech = candidate.get("technical", {})
    market = candidate.get("market", {})
    regime_name = (regime or {}).get("regime", "UNKNOWN")

    win_prob = _clamp(_safe_num(score.get("win_probability", 0)) / 100.0, 0.0, 0.95)
    rr = max(_safe_num(tech.get("atr_rr", 0)), 0.8)
    price = _safe_num(market.get("price", 0))
    stop = _safe_num(tech.get("aggressive_sl", tech.get("atr_sl", 0)))
    avg_win = max((_safe_num(tech.get("aggressive_tp1", tech.get("atr_tp1", price))) - price) / max(price, 1e-8) * 100.0, 0.1)
    avg_loss = max((price - stop) / max(price, 1e-8) * 100.0, 0.1)
    fractional_kelly = kelly_aggressive(win_prob * 100.0, avg_win, avg_loss)
    regime_cap = RETAIL_POSITION_CAP.get(regime_name, RETAIL_POSITION_CAP["UNKNOWN"])
    confidence = str(score.get("confidence", "Low")).lower()
    entry_quality = str(score.get("entry_quality", "WEAK")).upper()
    signal_count = int(score.get("aggressive_signal_count", tech.get("aggressive_signal_count", 0)))
    vol_ratio = _safe_num(market.get("vol_ratio", 0))
    base_risk_pct = adaptive_risk_pct(regime_name, 0, win_prob * 100.0)
    stop_pct = max((price - stop) / max(price, 1e-8), 0.01) if price > stop > 0 else 0.03

    confidence_mult = 1.0 if confidence == "high" else 0.8 if confidence == "medium" else 0.55
    quality_mult = 1.0 if entry_quality == "CLEAN" else 0.85 if entry_quality == "GOOD" else 0.6
    signal_mult = 1.0 if signal_count >= 2 else 0.85 if signal_count == 1 else 0.6
    liquidity_mult = 1.0 if vol_ratio >= 1.5 else 0.85 if vol_ratio >= 1.0 else 0.65

    suggested = min(base_risk_pct / stop_pct, regime_cap)
    suggested *= confidence_mult * quality_mult * signal_mult * liquidity_mult
    suggested = min(max(suggested, fractional_kelly), regime_cap)
    suggested = _clamp(suggested, 0.02, regime_cap)

    if suggested >= 0.12:
        band = "Max 12-15% modal"
    elif suggested >= 0.08:
        band = "Max 8-10% modal"
    elif suggested >= 0.05:
        band = "Max 5-7% modal"
    else:
        band = "Max 2-3% modal"

    note = (
        "Mode caution: kecilkan size 50% dan tunggu follow-through volume."
        if regime_name == "CAUTION"
        else "Jangan tambah saat rugi; naikkan size hanya jika setup berikutnya tetap clean."
    )
    return {
        "kelly_full_pct": round(min(fractional_kelly / 0.35 if fractional_kelly > 0 else 0.0, 0.5) * 100.0, 2),
        "kelly_fractional_pct": round(fractional_kelly * 100.0, 2),
        "risk_pct": round(base_risk_pct * 100.0, 2),
        "stop_distance_pct": round(stop_pct * 100.0, 2),
        "retail_position_pct": round(suggested * 100.0, 2),
        "retail_band": band,
        "note": note,
    }


def build_quant_shortlist(
    candidates: list[dict],
    regime: dict | None = None,
    max_candidates: int = 12,
) -> list[dict]:
    regime_name = (regime or {}).get("regime", "UNKNOWN")
    shortlist = []

    for candidate in candidates:
        score = candidate.get("score", {})
        tech = candidate.get("technical", {})
        flow = candidate.get("flow", {})
        market = candidate.get("market", {})

        base_score = _safe_num(score.get("total", 0))
        technical = _safe_num(score.get("technical", 0))
        flow_score = _safe_num(score.get("flow", 0))
        rr_score = _norm_ratio(_safe_num(tech.get("atr_rr", 0)), 1.0, 3.0)
        volume_score = _norm_ratio(_safe_num(market.get("vol_ratio", 0)), 1.0, 3.0)
        adx_score = _norm_ratio(_safe_num(tech.get("adx", 0)), 15.0, 40.0)
        rsi_score = _rsi_score(_safe_num(tech.get("rsi", 50)))
        liquidity_score = 100.0 if candidate.get("liquid") else 25.0
        signal_score = _norm_ratio(_safe_num(score.get("aggressive_signal_count", tech.get("aggressive_signal_count", 0))), 1.0, 3.0)

        quant_score = (
            base_score * QUANT_WEIGHTS["base_score"] +
            technical * QUANT_WEIGHTS["technical"] +
            flow_score * QUANT_WEIGHTS["flow"] +
            rr_score * QUANT_WEIGHTS["rr"] +
            volume_score * QUANT_WEIGHTS["volume"] +
            adx_score * QUANT_WEIGHTS["adx"] +
            rsi_score * QUANT_WEIGHTS["rsi"] +
            liquidity_score * QUANT_WEIGHTS["liquidity"] +
            signal_score * QUANT_WEIGHTS["signals"]
        )

        quant_score += _trend_bonus(candidate)

        five_cond = int(score.get("five_cond_count", 0))
        signal_count = int(score.get("aggressive_signal_count", tech.get("aggressive_signal_count", 0)))
        preferred_mode = str(score.get("preferred_entry_mode", tech.get("preferred_entry_mode", "WAIT"))).upper()
        if signal_count >= 2:
            quant_score += 4.0
        elif signal_count < 1 and five_cond < 3:
            quant_score -= 6.0
        if preferred_mode == "BREAKOUT":
            quant_score += 4.0
        elif preferred_mode == "PULLBACK":
            quant_score += 3.0
        elif preferred_mode == "MOMENTUM":
            quant_score += 2.0

        if _safe_num(flow.get("net_raw", 0)) < 0:
            quant_score -= 5.0

        if regime_name == "CAUTION":
            quant_score -= 4.0
        elif regime_name == "BEAR":
            quant_score -= 10.0

        candidate_copy = dict(candidate)
        candidate_copy["position_sizing"] = estimate_position_size(candidate, regime)
        candidate_copy["quant"] = {
            "score": round(_clamp(quant_score, 0.0, 100.0), 1),
            "regime": regime_name,
            "reasons": [
                f"Base {base_score:.1f}",
                f"Tech {technical:.1f}",
                f"Flow {flow_score:.1f}",
                f"R/R {_safe_num(tech.get('atr_rr', 0)):.2f}",
                f"Vol {_safe_num(market.get('vol_ratio', 0)):.2f}x",
                f"Signal {signal_count}/3",
                f"Mode {preferred_mode}",
            ],
        }
        shortlist.append(candidate_copy)

    shortlist.sort(
        key=lambda item: (
            item.get("quant", {}).get("score", 0),
            item.get("score", {}).get("total", 0),
            item.get("score", {}).get("aggressive_signal_count", 0),
            item.get("technical", {}).get("atr_rr", 0),
        ),
        reverse=True,
    )

    for idx, candidate in enumerate(shortlist[:max_candidates], start=1):
        candidate["quant"]["rank"] = idx

    return shortlist[:max_candidates]


def extract_selected_tickers(text: str, universe: Iterable[str]) -> list[str]:
    upper_text = text.upper()
    found = []
    for ticker in universe:
        token = ticker.upper().strip()
        if token and re.search(rf"\b{re.escape(token)}\b", upper_text):
            found.append(token)
    return found[:3]
