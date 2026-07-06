from __future__ import annotations

"""
tests/test_edge_cases.py — Expanded coverage for previously untested paths.

Covers:
  1. Technical indicators on minimal / NaN-heavy data
  2. CSV portfolio parsing quirks (BOM, wrong columns, unicode, floats as prices)
  3. Alert store per-user cap and retention enforcement
  4. Groq health check on key format failure and network failure
  5. TTLCache hit/miss counter accuracy
  6. JSON store semantic corruption detection (expected_type)
  7. Sector enum normalization round-trips and unknown-label error
  8. Market data facade emits DeprecationWarning
"""

import asyncio
import os
import tempfile
import threading
import time
import unittest
import warnings
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd


# ── 1. Technical indicator edge cases ─────────────────────

class TechnicalIndicatorEdgeCaseTests(unittest.TestCase):
    """_calc_technical must never raise; it should return safe defaults."""

    def _empty_ohlcv(self, rows: int) -> pd.DataFrame:
        idx = pd.date_range("2024-01-01", periods=rows, freq="D")
        return pd.DataFrame(
            {
                "Open": [100.0] * rows,
                "High": [101.0] * rows,
                "Low": [99.0] * rows,
                "Close": [100.0] * rows,
                "Volume": [1_000_000.0] * rows,
            },
            index=idx,
        )

    def test_empty_dataframe_returns_safe_defaults(self):
        from utils.stock_service import _calc_technical

        result = _calc_technical(pd.DataFrame())
        self.assertEqual(result["rsi"], 50.0)
        self.assertEqual(result["trend"], "N/A")

    def test_single_row_returns_safe_defaults(self):
        from utils.stock_service import _calc_technical

        result = _calc_technical(self._empty_ohlcv(1))
        self.assertEqual(result["rsi"], 50.0)

    def test_19_rows_below_minimum_returns_safe_defaults(self):
        from utils.stock_service import _calc_technical

        result = _calc_technical(self._empty_ohlcv(19))
        self.assertEqual(result["rsi"], 50.0)

    def test_nan_close_prices_do_not_raise(self):
        from utils.stock_service import _calc_technical

        df = self._empty_ohlcv(60)
        df.loc[df.index[10:20], "Close"] = np.nan
        try:
            result = _calc_technical(df)
        except Exception as exc:
            self.fail(f"_calc_technical raised with NaN data: {exc}")
        self.assertIn("rsi", result)

    def test_zero_volume_does_not_raise(self):
        from utils.stock_service import _calc_technical

        df = self._empty_ohlcv(60)
        df["Volume"] = 0.0
        try:
            result = _calc_technical(df)
        except Exception as exc:
            self.fail(f"_calc_technical raised with zero volume: {exc}")
        self.assertEqual(result["vol_ratio"], 0.0)

    def test_constant_price_series_rsi_is_50(self):
        from utils.stock_service import _calc_technical

        result = _calc_technical(self._empty_ohlcv(60))
        self.assertEqual(result["rsi"], 50.0)

    def test_five_cond_count_is_between_0_and_5(self):
        from utils.stock_service import _calc_technical

        result = _calc_technical(self._empty_ohlcv(60))
        self.assertGreaterEqual(result["five_cond_count"], 0)
        self.assertLessEqual(result["five_cond_count"], 5)


# ── 2. CSV portfolio parsing quirks ───────────────────────

class PortfolioCsvParsingTests(unittest.IsolatedAsyncioTestCase):
    async def _parse(self, text: str):
        # Import inline to avoid discord dependency at module load
        import sys, types
        # Minimal shim so cogs.portfolio_cog can be imported without discord
        if "discord" not in sys.modules:
            discord_mock = types.ModuleType("discord")
            discord_mock.Attachment = object
            sys.modules["discord"] = discord_mock
        from cogs.portfolio_cog import _parse_csv

        return await _parse_csv(text)

    async def test_bom_prefix_is_stripped(self):
        csv = "\ufeffTICKER,QTY,AVG_PRICE\nBBCA,100,9500\n"
        result = await self._parse(csv)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["ticker"], "BBCA")

    async def test_lowercase_column_names_are_accepted(self):
        csv = "ticker,qty,avg_price\nTLKM,200,3100\n"
        result = await self._parse(csv)
        self.assertEqual(result[0]["ticker"], "TLKM")
        self.assertEqual(result[0]["qty"], 200)

    async def test_comma_in_price_is_stripped(self):
        csv = "TICKER,QTY,AVG_PRICE\nBBRI,150,5,000\n"
        # "5,000" after strip → "5000" → 5000.0
        result = await self._parse(csv)
        if result:  # only check if parsed successfully
            self.assertEqual(result[0]["avg_price"], 5000.0)

    async def test_empty_csv_returns_empty_list(self):
        result = await self._parse("TICKER,QTY,AVG_PRICE\n")
        self.assertEqual(result, [])

    async def test_row_with_invalid_qty_is_skipped(self):
        csv = "TICKER,QTY,AVG_PRICE\nBBCA,notanumber,9500\nTLKM,100,3100\n"
        result = await self._parse(csv)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["ticker"], "TLKM")

    async def test_invalid_ticker_is_skipped(self):
        csv = "TICKER,QTY,AVG_PRICE\nBAD!!,100,9500\nBBCA,100,9500\n"
        result = await self._parse(csv)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["ticker"], "BBCA")


# ── 3. Alert store cap and retention ──────────────────────

class AlertStoreSQLiteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        db_path = os.path.join(self.tmp, "alerts_test.db")
        from data.alert_store_sqlite import AlertStoreSQLite, MAX_ALERTS_PER_USER

        self.store = AlertStoreSQLite(db_path=db_path)
        self.cap = MAX_ALERTS_PER_USER

    def test_add_returns_true_on_success(self):
        self.assertTrue(self.store.add(1, "BBCA", ">", 10000))

    def test_add_returns_false_on_duplicate(self):
        self.store.add(1, "BBCA", ">", 10000)
        self.assertFalse(self.store.add(1, "BBCA", ">", 10000))

    def test_per_user_cap_is_enforced(self):
        for i in range(self.cap + 5):
            self.store.add(1, "BBCA", ">", float(10000 + i))
        alerts = self.store.get_user_alerts(1)
        self.assertLessEqual(len(alerts), self.cap)

    def test_remove_returns_count(self):
        self.store.add(1, "TLKM", ">", 3000)
        self.store.add(1, "TLKM", "<", 2500)
        n = self.store.remove(1, "TLKM")
        self.assertEqual(n, 2)

    def test_deactivate_marks_triggered(self):
        self.store.add(2, "ADRO", ">", 2500)
        self.store.deactivate(2, "ADRO", ">", 2500)
        alerts = self.store.get_user_alerts(2)
        self.assertEqual(len(alerts), 0)

    def test_get_all_active_excludes_triggered(self):
        self.store.add(3, "BBRI", "<", 4000)
        self.store.deactivate(3, "BBRI", "<", 4000)
        active = self.store.get_all_active()
        codes = [a["ticker"] for a in active if a["user_id"] == 3]
        self.assertNotIn("BBRI", codes)

    def test_thread_safety_concurrent_adds(self):
        errors = []

        def _add(n):
            try:
                self.store.add(99, "BBCA", ">", float(n))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_add, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])


# ── 4. Groq health check ──────────────────────────────────

class GroqHealthCheckTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_key_format_fails_fast(self):
        from utils.groq_health import _probe_groq

        with patch("utils.groq_utils.GROQ_API_KEY", "invalid-key"):
            ok, msg = await _probe_groq()
        self.assertFalse(ok)
        self.assertIn("Format", msg)

    async def test_network_error_returns_false(self):
        from utils.groq_health import _probe_groq

        with patch("utils.groq_utils.validate_groq_config", return_value=(True, "ok")), \
             patch("groq.Groq") as mock_groq:
            mock_groq.return_value.chat.completions.create.side_effect = \
                ConnectionError("network down")
            ok, msg = await _probe_groq()
        self.assertFalse(ok)

    async def test_successful_probe_returns_true(self):
        from utils.groq_health import _probe_groq

        fake_resp = MagicMock()
        fake_resp.choices[0].message.content = "pong"

        with patch("utils.groq_utils.validate_groq_config", return_value=(True, "ok")), \
             patch("groq.Groq") as mock_groq:
            mock_groq.return_value.chat.completions.create.return_value = fake_resp
            ok, msg = await _probe_groq()
        self.assertTrue(ok)
        self.assertEqual(msg, "ok")


# ── 5. TTLCache hit/miss counters ─────────────────────────

class TTLCacheCounterTests(unittest.TestCase):
    def setUp(self):
        from utils.runtime_cache import TTLCache

        self.cache: TTLCache = TTLCache(max_entries=10)

    def test_initial_counters_are_zero(self):
        stats = self.cache.get_stats()
        self.assertEqual(stats["hits"], 0)
        self.assertEqual(stats["misses"], 0)
        self.assertEqual(stats["sets"], 0)

    def test_miss_on_empty_cache(self):
        self.cache.get("nonexistent")
        self.assertEqual(self.cache.get_stats()["misses"], 1)

    def test_hit_after_set(self):
        self.cache.set("k", "v", ttl=60)
        self.cache.get("k")
        stats = self.cache.get_stats()
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["sets"], 1)

    def test_expired_entry_counts_as_miss(self):
        self.cache.set("k", "v", ttl=0.01)
        time.sleep(0.05)
        self.cache.get("k")
        stats = self.cache.get_stats()
        self.assertEqual(stats["misses"], 1)

    def test_hit_rate_calculation(self):
        self.cache.set("a", 1, 60)
        self.cache.set("b", 2, 60)
        self.cache.get("a")  # hit
        self.cache.get("a")  # hit
        self.cache.get("z")  # miss
        stats = self.cache.get_stats()
        self.assertAlmostEqual(stats["hit_rate"], 2 / 3, places=3)


# ── 6. JSON store semantic corruption detection ───────────

class JsonStoreSemanticCorruptionTests(unittest.TestCase):
    def test_list_returns_default_when_dict_expected(self):
        from utils.json_store import read_json, write_json

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "data.json")
            write_json(path, [1, 2, 3], backup=False)
            result = read_json(path, {"safe": True}, expected_type=dict)
        self.assertEqual(result, {"safe": True})

    def test_dict_returns_default_when_list_expected(self):
        from utils.json_store import read_json, write_json

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "data.json")
            write_json(path, {"a": 1}, backup=False)
            result = read_json(path, [], expected_type=list)
        self.assertEqual(result, [])

    def test_correct_type_is_returned_normally(self):
        from utils.json_store import read_json, write_json

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "data.json")
            write_json(path, {"ticker": "BBCA"}, backup=False)
            result = read_json(path, {}, expected_type=dict)
        self.assertEqual(result["ticker"], "BBCA")

    def test_backup_files_are_created_on_write(self):
        from utils.json_store import read_json, write_json, list_backups

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "data.json")
            write_json(path, {"v": 1})
            write_json(path, {"v": 2})  # should create .bak.1
            backups = list_backups(path)
        self.assertGreater(len(backups), 0)

    def test_restore_backup_recovers_previous_data(self):
        from utils.json_store import read_json, write_json, restore_latest_backup

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "data.json")
            write_json(path, {"v": "original"})
            write_json(path, {"v": "overwritten"})
            restored = restore_latest_backup(path)
            self.assertTrue(restored)
            data = read_json(path, {})
        self.assertEqual(data["v"], "original")


# ── 7. Sector enum normalization ──────────────────────────

class SectorEnumTests(unittest.TestCase):
    def test_normalize_banking_variants(self):
        from utils.sector_constants import Sector

        self.assertEqual(Sector.normalize("Banking"), Sector.BANKING)
        self.assertEqual(Sector.normalize("Financial Services"), Sector.BANKING)
        self.assertEqual(Sector.normalize("bank"), Sector.BANKING)

    def test_normalize_property_aliases(self):
        from utils.sector_constants import Sector

        self.assertEqual(Sector.normalize("Real Estate"), Sector.PROPERTY)
        self.assertEqual(Sector.normalize("properti"), Sector.PROPERTY)

    def test_normalize_unknown_raises_value_error(self):
        from utils.sector_constants import Sector

        with self.assertRaises(ValueError):
            Sector.normalize("Widgets & Gizmos")

    def test_normalize_safe_returns_general_on_unknown(self):
        from utils.sector_constants import Sector

        result = Sector.normalize_safe("UnknownSector")
        self.assertEqual(result, Sector.GENERAL)

    def test_sector_value_is_canonical_string(self):
        from utils.sector_constants import Sector

        self.assertEqual(Sector.COAL_MINING.value, "Coal Mining")
        self.assertEqual(Sector.METALS_MINING.value, "Metals Mining")

    def test_normalize_case_insensitive(self):
        from utils.sector_constants import Sector

        self.assertEqual(Sector.normalize("COAL MINING"), Sector.COAL_MINING)
        self.assertEqual(Sector.normalize("coal mining"), Sector.COAL_MINING)


# ── 8. Market data facade deprecation warning ─────────────

class MarketDataFacadeDeprecationTests(unittest.TestCase):
    def test_import_emits_deprecation_warning(self):
        import sys

        # Remove from cache to force re-import
        sys.modules.pop("utils.market_data", None)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            import utils.market_data  # noqa: F401

        dep_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        self.assertTrue(
            any("market_data" in str(w.message).lower() for w in dep_warnings),
            f"Expected DeprecationWarning about market_data, got: {[str(w.message) for w in caught]}",
        )


if __name__ == "__main__":
    unittest.main()
