from __future__ import annotations

from config import MAX_RISK_PER_TRADE_PCT


def kelly_aggressive(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
    fraction: float = 0.35,
) -> float:
    """
    Fractional Kelly untuk sizing agresif tapi tetap dibatasi.
    """
    if avg_loss <= 0:
        return 0.02
    p = win_rate / 100
    b = avg_win / avg_loss
    full_kelly = max(0.0, (p * b - (1 - p)) / (b + 1e-8))
    return round(min(full_kelly * fraction, 0.15), 4)


def calc_position_size(
    capital: float,
    price: float,
    sl_price: float,
    risk_pct: float = 0.02,
) -> dict[str, float | int]:
    """
    Hitung ukuran posisi berbasis risk per trade untuk lot IDX.
    """
    if price <= 0 or sl_price <= 0 or price <= sl_price or capital <= 0:
        return {"shares": 0, "lots": 0, "risk_amount": 0}

    risk_pct = min(max(risk_pct, 0.0), MAX_RISK_PER_TRADE_PCT)
    risk_per_share = price - sl_price
    risk_amount = capital * risk_pct
    shares = int(risk_amount / risk_per_share)
    lots = max(1, shares // 100)
    actual_shares = lots * 100
    actual_risk = actual_shares * risk_per_share

    return {
        "lots": lots,
        "shares": actual_shares,
        "risk_amount": round(actual_risk, 0),
        "risk_pct_actual": round(actual_risk / capital * 100, 2),
        "position_value": round(actual_shares * price, 0),
        "position_pct": round(actual_shares * price / capital * 100, 2),
    }


def adaptive_risk_pct(
    regime: str,
    consecutive_losses: int,
    recent_win_rate: float,
) -> float:
    """
    Anti-martingale sizing: kecilkan saat drawdown, naikkan tipis saat performa bagus.
    """
    base = 0.02

    if regime == "BEAR":
        base = 0.01
    elif regime == "CAUTION":
        base = 0.015

    if consecutive_losses >= 3:
        base *= 0.5
    elif consecutive_losses >= 2:
        base *= 0.7

    if consecutive_losses == 0 and recent_win_rate > 65:
        base = min(base * 1.2, 0.025)

    return round(min(base, MAX_RISK_PER_TRADE_PCT), 4)
