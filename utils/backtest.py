from __future__ import annotations

"""
utils/backtest.py — Legacy backtest shim (v3 compatibility wrapper).

All heavy lifting has been moved to backtest/engine_backtest.py (v4 engine).
This file is kept only so that any external code importing
``utils.backtest.run_backtest`` continues to work.

The duplicate indicator functions (_calc_rsi_series, _calc_adx_series, etc.)
that previously lived here have been removed — use the canonical
implementations in utils/stock_service.py instead.

Scheduled for removal: v5.0.0
"""

import warnings

warnings.warn(
    (
        "utils.backtest is deprecated. "
        "Use backtest.engine_backtest.run_backtest_v4 instead. "
        "This module will be removed in v5."
    ),
    DeprecationWarning,
    stacklevel=2,
)

import asyncio
from typing import Any

from utils.ticker_utils import normalize_ticker


def run_backtest(
    ticker: str,
    months: int = 12,
    sl_mult: float = 2.0,
    tp_mult: float = 3.0,
    use_regime_filter: bool = True,
) -> dict[str, Any]:
    """
    Thin synchronous wrapper around the v4 async engine.

    Parameters match the old v3 signature for drop-in compatibility.
    """
    try:
        ticker = normalize_ticker(ticker)
    except ValueError as exc:
        return {"error": str(exc)}

    from backtest.engine_backtest import run_backtest_v4

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If called from inside an async context use a thread executor
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    asyncio.run,
                    run_backtest_v4(
                        ticker,
                        months=months,
                        sl_mult=sl_mult,
                        tp_mult=tp_mult,
                        use_regime=use_regime_filter,
                    ),
                )
                return future.result(timeout=120)
        else:
            return loop.run_until_complete(
                run_backtest_v4(
                    ticker,
                    months=months,
                    sl_mult=sl_mult,
                    tp_mult=tp_mult,
                    use_regime=use_regime_filter,
                )
            )
    except Exception as exc:
        return {"error": str(exc)}


# Keep _get_ihsg_regime as a private compat alias (used only by the old
# legacy cog which is already deprecated).  Do NOT import or use it in
# new code.
def _get_ihsg_regime(start, end) -> dict:  # type: ignore[override]
    """Deprecated — use utils.market_regime.get_ihsg_regime() directly."""
    from utils.market_regime import get_ihsg_regime

    return get_ihsg_regime()
