from __future__ import annotations


def safe_number(val, default=0.0):
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def normalize_der(value, sector_label: str = "") -> float:
    der = safe_number(value, 0.0)
    if der <= 0:
        return 0.0
    sector = (sector_label or "").strip().lower()
    is_banking = sector == "banking"
    if der >= 100.0:
        return round(der / 100.0, 2)
    if is_banking:
        return round(der, 2)
    if der > 20.0:
        return round(der / 100.0, 2)
    return round(der, 2)
