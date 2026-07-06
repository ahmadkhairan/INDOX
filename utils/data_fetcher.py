from __future__ import annotations

"""Compatibility facade for legacy imports.

This module used to duplicate large portions of the market, scanning, and
technical-analysis stack. The canonical implementations now live in the
specialized modules below, and this file re-exports them to keep old imports
working without maintaining a second copy of the logic.
"""

from utils.market_regime import get_coal_price, get_ihsg, get_ihsg_regime, get_top_movers
from utils.news_utils import detect_special_news, get_berita
from utils.scan_utils import IDX_UNIVERSE, scan_all_liquid_idx, scan_watchlist
from utils.scoring_rules import compute_score as _compute_score
from utils.sector_data import SECTOR_MAP, SECTOR_NORMS, get_sector_context
from utils.stock_service import (
    _calc_technical,
    _fetch_history,
    _fmt_idr,
    _fmt_lot,
    _get_flow,
    _get_yfinance_data,
    _merge_fundamental,
    _safe,
    _yf_info_with_retry,
    get_sectors_financials,
    get_sectors_flow,
    get_sectors_stock,
    get_stock_data,
)


def _legacy_get_stock_data_impl(ticker: str) -> dict:
    return get_stock_data(ticker)


__all__ = [
    "IDX_UNIVERSE",
    "SECTOR_MAP",
    "SECTOR_NORMS",
    "_calc_technical",
    "_compute_score",
    "_fetch_history",
    "_fmt_idr",
    "_fmt_lot",
    "_get_flow",
    "_get_yfinance_data",
    "_legacy_get_stock_data_impl",
    "_merge_fundamental",
    "_safe",
    "_yf_info_with_retry",
    "detect_special_news",
    "get_berita",
    "get_coal_price",
    "get_ihsg",
    "get_ihsg_regime",
    "get_sector_context",
    "get_sectors_financials",
    "get_sectors_flow",
    "get_sectors_stock",
    "get_stock_data",
    "get_top_movers",
    "scan_all_liquid_idx",
    "scan_watchlist",
]
