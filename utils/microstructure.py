# utils/microstructure.py — BEI / IDX Market Microstructure Rules
from __future__ import annotations

import math
from typing import Literal

RoundingMode = Literal["nearest", "floor", "down", "ceil", "up"]


def get_bei_tick_size(price: float) -> int:
    """Return the official BEI (IDX) tick size (fraksi harga) for a given price.

    Peraturan BEI No. II-A (Fraksi Harga Saham):
    - Kelompok 1: Harga < Rp 200          -> Fraksi Rp 1
    - Kelompok 2: Harga Rp 200 - < Rp 500  -> Fraksi Rp 2
    - Kelompok 3: Harga Rp 500 - < Rp 2000 -> Fraksi Rp 5
    - Kelompok 4: Harga Rp 2000 - < Rp 5000-> Fraksi Rp 10
    - Kelompok 5: Harga >= Rp 5000        -> Fraksi Rp 25
    """
    if price < 200:
        return 1
    elif price < 500:
        return 2
    elif price < 2000:
        return 5
    elif price < 5000:
        return 10
    else:
        return 25


def round_to_tick(price: float, mode: RoundingMode = "nearest") -> float:
    """Round a price to a valid BEI (IDX) tick size.

    Args:
        price: Raw target price.
        mode:
            - 'nearest': round to nearest valid tick
            - 'floor' / 'down': round down (useful for stop loss on long positions)
            - 'ceil' / 'up': round up (useful for take profit on long positions)

    Returns:
        float: Price rounded to valid BEI tick.
    """
    if price <= 0:
        return 0.0

    tick = get_bei_tick_size(price)

    if mode in ("floor", "down"):
        rounded = math.floor(price / tick) * tick
    elif mode in ("ceil", "up"):
        rounded = math.ceil(price / tick) * tick
    else:
        rounded = round(price / tick) * tick

    # In case rounding pushed the price across a tier boundary with different tick size,
    # re-validate against the new price tier.
    new_tick = get_bei_tick_size(rounded)
    if new_tick != tick and rounded > 0:
        if mode in ("floor", "down"):
            rounded = math.floor(rounded / new_tick) * new_tick
        elif mode in ("ceil", "up"):
            rounded = math.ceil(rounded / new_tick) * new_tick
        else:
            rounded = round(rounded / new_tick) * new_tick

    return float(max(rounded, 1.0))


def calc_lot_value(price: float, lots: int) -> float:
    """Calculate total monetary value of an IDX position.

    In IDX, 1 lot = 100 shares.
    """
    if price <= 0 or lots <= 0:
        return 0.0
    return float(price * lots * 100)


def get_ara_limit(prev_close: float) -> float:
    """Return Auto Rejection Atas (ARA) price limit for standard regular board."""
    if prev_close <= 0:
        return 0.0
    if prev_close < 200:
        pct = 0.35
    elif prev_close <= 5000:
        pct = 0.25
    else:
        pct = 0.20
    return round_to_tick(prev_close * (1.0 + pct), mode="floor")


def get_arb_limit(prev_close: float) -> float:
    """Return Auto Rejection Bawah (ARB) price limit for standard regular board (symmetric)."""
    if prev_close <= 0:
        return 0.0
    if prev_close < 200:
        pct = 0.35
    elif prev_close <= 5000:
        pct = 0.25
    else:
        pct = 0.20
    # Minimum regular price is 50
    raw_arb = max(prev_close * (1.0 - pct), 50.0)
    return round_to_tick(raw_arb, mode="ceil")
