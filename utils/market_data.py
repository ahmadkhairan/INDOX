from __future__ import annotations

"""
utils/market_data.py — DEPRECATED facade.

This module is a thin re-export shim kept alive only for backward
compatibility.  It will be removed in v5.

Migration guide
---------------
Replace every ``from utils.market_data import X`` with the canonical
import listed below:

    detect_special_news, get_berita      -> from utils.news_utils import …
    get_coal_price, get_ihsg,
    get_ihsg_regime, get_top_movers      -> from utils.market_regime import …
    get_stock_data                       -> from utils.stock_service import …
    get_sector_context                   -> from utils.sector_data import …
    scan_all_liquid_idx, scan_watchlist  -> from utils.scan_utils import …

Scheduled for removal: v5.0.0
"""

import warnings

warnings.warn(
    (
        "utils.market_data is deprecated and will be removed in v5. "
        "Import directly from utils.news_utils, utils.market_regime, "
        "utils.stock_service, utils.sector_data, or utils.scan_utils."
    ),
    DeprecationWarning,
    stacklevel=2,
)

from utils.market_regime import get_coal_price, get_ihsg, get_ihsg_regime, get_top_movers  # noqa: E402,F401
from utils.news_utils import detect_special_news, get_berita  # noqa: E402,F401
from utils.scan_utils import scan_all_liquid_idx, scan_watchlist  # noqa: E402,F401
from utils.sector_data import get_sector_context  # noqa: E402,F401
from utils.stock_service import get_stock_data  # noqa: E402,F401

__all__ = [
    "detect_special_news",
    "get_berita",
    "get_coal_price",
    "get_ihsg",
    "get_ihsg_regime",
    "get_stock_data",
    "get_top_movers",
    "get_sector_context",
    "scan_all_liquid_idx",
    "scan_watchlist",
]
