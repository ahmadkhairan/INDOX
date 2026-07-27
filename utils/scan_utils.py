from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional

from config import MIN_DAILY_VALUE_B, MIN_MARKET_CAP_T
from config import MIN_ENTRY_CONDITIONS
from utils.runtime_cache import TTLCache
from utils.ticker_utils import IDX_UNIVERSE
from utils.yf_guard import YFinanceUnavailable, get_fast_info


TICKERS_CACHE_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "all_liquid_tickers.json")
SCAN_RESULTS_TTL_SECONDS = 120.0
WATCHLIST_RESULTS_TTL_SECONDS = 60.0
_scan_results_cache = TTLCache[list](max_entries=24)


def _safe(val, default=0.0):
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _load_ticker_cache() -> dict:
    try:
        if os.path.exists(TICKERS_CACHE_FILE):
            with open(TICKERS_CACHE_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_ticker_cache(data: dict):
    try:
        os.makedirs(os.path.dirname(TICKERS_CACHE_FILE), exist_ok=True)
        with open(TICKERS_CACHE_FILE, "w") as f:
            json.dump(data, f)
    except Exception as exc:
        print(f"[TickerCache] ⚠️ {exc}")


def _quick_screen(ticker: str) -> Optional[dict]:
    try:
        fast = get_fast_info(f"{ticker}.JK")
        price = float(fast.last_price or 0)
        mktcap = float(fast.market_cap or 0)
        vol = float(fast.three_month_average_volume or 0)
        if price <= 0 or mktcap <= 0:
            return None

        mktcap_t = mktcap / 1e12
        daily_val_b = (price * vol) / 1e9
        if mktcap_t < MIN_MARKET_CAP_T or daily_val_b < MIN_DAILY_VALUE_B:
            return None
        return {"ticker": ticker, "price": price, "mktcap_t": mktcap_t, "daily_val_b": daily_val_b}
    except YFinanceUnavailable:
        raise
    except Exception:
        return None


def scan_all_liquid_idx(
    min_vol_ratio: float = 1.0,
    min_foreign_lot: float = 0,
    min_adx: float = 15.0,
    top_n: int = 30,
    min_score: float = 60.0,
) -> list:
    from utils.stock_service import get_stock_data

    cache_key = ("scan_all_liquid_idx", round(min_vol_ratio, 3), round(min_foreign_lot, 3), round(min_adx, 3), int(top_n), round(min_score, 3))
    cached = _scan_results_cache.get(cache_key)
    if cached is not None:
        print(f"[scan_all] ✅ Full scan cache hit: {len(cached)} kandidat")
        return cached

    today = datetime.now().strftime("%Y-%m-%d")
    cache = _load_ticker_cache()
    cache_matches_filters = (
        cache.get("market_cap_t") == MIN_MARKET_CAP_T
        and cache.get("daily_value_b") == MIN_DAILY_VALUE_B
    )
    if cache.get("date") == today and cache.get("passed_quick") and cache_matches_filters:
        passed_quick = cache["passed_quick"]
        print(f"[scan_all] ✅ Quick screen cache hit: {len(passed_quick)} tickers")
    else:
        print(f"[scan_all] 🔍 Quick screening {len(IDX_UNIVERSE)} tickers...")
        passed_quick = []
        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = {ex.submit(_quick_screen, ticker): ticker for ticker in IDX_UNIVERSE}
            for fut in as_completed(futures):
                try:
                    result = fut.result(timeout=15)
                    if result:
                        passed_quick.append(result["ticker"])
                except YFinanceUnavailable:
                    raise
                except Exception:
                    pass
        _save_ticker_cache({
            "date": today,
            "passed_quick": passed_quick,
            "market_cap_t": MIN_MARKET_CAP_T,
            "daily_value_b": MIN_DAILY_VALUE_B,
        })
        print(f"[scan_all] ✅ Quick screen selesai: {len(passed_quick)} lolos filter")

    print(f"[scan_all] 📊 Full analysis {len(passed_quick)} tickers (8 workers)...")
    results = []
    analyzed = []
    reject_counts = {
        "provider_error": 0,
        "score": 0,
        "signal": 0,
        "adx": 0,
        "conditions": 0,
        "foreign_flow": 0,
        "volume": 0,
    }
    error_examples = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(get_stock_data, ticker): ticker for ticker in passed_quick}
        for fut in as_completed(futures):
            ticker = futures[fut]
            try:
                data = fut.result(timeout=30)
                if "error" in data:
                    reject_counts["provider_error"] += 1
                    if len(error_examples) < 3:
                        error_examples.append(f"{ticker}: {data.get('error', 'unknown error')}")
                    continue

                tech = data.get("technical", {})
                flow = data.get("flow", {})
                market = data.get("market", {})
                score = data.get("score", {})
                analyzed.append(data)
                score_total = _safe(score.get("total", 0))
                five_cond = int(score.get("five_cond_count", 0))
                signal_count = int(score.get("aggressive_signal_count", 0))
                preferred_mode = str(score.get("preferred_entry_mode", tech.get("preferred_entry_mode", "WAIT"))).upper()

                if score_total < min_score:
                    reject_counts["score"] += 1
                    continue
                if signal_count < 1:
                    reject_counts["signal"] += 1
                    continue
                if _safe(tech.get("adx", 0)) < min_adx and preferred_mode != "PULLBACK":
                    reject_counts["adx"] += 1
                    continue
                if score_total >= 70 and five_cond < MIN_ENTRY_CONDITIONS and preferred_mode == "MOMENTUM":
                    reject_counts["conditions"] += 1
                    continue
                if score_total < 70 and five_cond < (MIN_ENTRY_CONDITIONS + 1) and preferred_mode == "MOMENTUM":
                    reject_counts["conditions"] += 1
                    continue
                if _safe(flow.get("net_raw", 0)) < min_foreign_lot:
                    reject_counts["foreign_flow"] += 1
                    continue
                if _safe(market.get("vol_ratio", 0)) < min_vol_ratio:
                    reject_counts["volume"] += 1
                    continue
                results.append(data)
            except Exception as exc:
                reject_counts["provider_error"] += 1
                if len(error_examples) < 3:
                    error_examples.append(f"{ticker}: {exc}")
                print(f"[scan_all] ⚠️ {ticker}: {exc}")

    results.sort(key=lambda item: item["score"]["total"], reverse=True)
    rejected_summary = ", ".join(f"{key}={value}" for key, value in reject_counts.items() if value)
    print(f"[scan_all] Full analysis filter summary: {rejected_summary or 'none'}")
    if error_examples:
        print(f"[scan_all] Error examples: {' | '.join(error_examples)}")
    if not results and analyzed:
        # Safety fallback: keep the scanner useful on weak market days while
        # preserving transparency. These candidates did not pass every
        # strict entry gate and are marked for the downstream prompt.
        fallback = sorted(
            analyzed,
            key=lambda item: (
                _safe(item.get("score", {}).get("total", 0)),
                _safe(item.get("score", {}).get("aggressive_signal_count", 0)),
                _safe(item.get("technical", {}).get("adx", 0)),
            ),
            reverse=True,
        )[:top_n]
        for item in fallback:
            item["scan_fallback"] = True
        results = fallback
        print(f"[scan_all] Fallback shortlist aktif: {len(results)} kandidat terbaik, filter strict tidak terpenuhi")
    print(f"[scan_all] 🏆 Top {min(top_n, len(results))} dari {len(results)} kandidat lolos semua filter")
    return _scan_results_cache.set(cache_key, results[:top_n], SCAN_RESULTS_TTL_SECONDS)


def scan_watchlist(tickers: list) -> list:
    from utils.stock_service import get_stock_data

    cache_key = ("scan_watchlist", tuple(normalized for normalized in tickers))
    cached = _scan_results_cache.get(cache_key)
    if cached is not None:
        print(f"[scan_watchlist] ✅ Cache hit: {len(cached)} kandidat")
        return cached

    results = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(get_stock_data, ticker): ticker for ticker in tickers}
        for future in as_completed(futures):
            try:
                data = future.result(timeout=30)
                if "error" not in data:
                    results.append(data)
            except Exception as exc:
                ticker = futures[future]
                print(f"[scan_watchlist] ⚠️ {ticker}: {exc}")

    results.sort(key=lambda item: item["score"]["total"], reverse=True)
    return _scan_results_cache.set(cache_key, results, WATCHLIST_RESULTS_TTL_SECONDS)
