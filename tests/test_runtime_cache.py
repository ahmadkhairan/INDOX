from __future__ import annotations

import unittest
from unittest.mock import patch

from utils.runtime_cache import TTLCache
from utils import stock_service


class RuntimeCacheTests(unittest.TestCase):
    def test_cache_returns_deep_copy(self):
        cache = TTLCache[list](max_entries=4)
        cache.set("items", [{"ticker": "BBCA"}], ttl=60)

        first = cache.get("items")
        second = cache.get("items")
        first[0]["ticker"] = "BBRI"

        self.assertEqual(second[0]["ticker"], "BBCA")

    def test_get_or_set_builds_once(self):
        cache = TTLCache[int](max_entries=4)
        calls = {"count": 0}

        def _builder():
            calls["count"] += 1
            return 42

        first = cache.get_or_set("answer", 60, _builder)
        second = cache.get_or_set("answer", 60, _builder)

        self.assertEqual(first, 42)
        self.assertEqual(second, 42)
        self.assertEqual(calls["count"], 1)


class StockServiceCacheTests(unittest.TestCase):
    def setUp(self):
        stock_service._stock_data_cache.clear()

    def test_get_stock_data_reuses_recent_analysis(self):
        yf_data = {
            "company_name": "Bank Central Asia",
            "sector": "Banking",
            "industry": "Banks",
            "market": {"volume": 100.0, "avg_volume": 50.0, "value_raw": 20_000_000_000.0},
            "technical": {"adx": 25.0},
        }
        fundamental = {"market_cap_raw": 10_000_000_000_000.0, "pbv": 2.0}
        flow = {"net_raw": 0.0}
        score = {"total": 75.0, "five_cond_count": 4}
        sector_ctx = {"label": "Banking"}

        with patch.object(stock_service, "get_sectors_stock", return_value=None), \
             patch.object(stock_service, "get_sectors_financials", return_value=None), \
             patch.object(stock_service, "_get_yfinance_data", return_value=yf_data) as yf_mock, \
             patch.object(stock_service, "_merge_fundamental", return_value=fundamental), \
             patch.object(stock_service, "_get_flow", return_value=flow), \
             patch.object(stock_service, "get_sector_context", return_value=sector_ctx), \
             patch.object(stock_service, "compute_score", return_value=score):
            first = stock_service.get_stock_data("BBCA")
            second = stock_service.get_stock_data("BBCA")

        self.assertEqual(first["ticker"], "BBCA")
        self.assertEqual(second["score"]["total"], 75.0)
        self.assertEqual(yf_mock.call_count, 1)


if __name__ == "__main__":
    unittest.main()
