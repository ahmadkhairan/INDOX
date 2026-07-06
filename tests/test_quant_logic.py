from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from data import picks_tracker
from memory import vector_memory
from risk.engine import RiskEngine, _normalize_sector_label
from utils import stock_service


class IndicatorMathTests(unittest.TestCase):
    def setUp(self):
        close = pd.Series(np.linspace(100, 140, 60))
        high = close + 2
        low = close - 2
        volume = pd.Series(np.linspace(1_000_000, 2_000_000, 60))
        self.df = pd.DataFrame(
            {
                "Open": close - 1,
                "High": high,
                "Low": low,
                "Close": close,
                "Volume": volume,
            }
        )

    def test_rsi_returns_bullish_value_on_uptrend(self):
        value = stock_service._rsi(self.df["Close"])
        self.assertGreaterEqual(value, 60.0)
        self.assertLessEqual(value, 100.0)

    def test_macd_histogram_positive_on_uptrend(self):
        _, _, hist = stock_service._macd(self.df["Close"])
        self.assertGreater(hist, 0.0)

    def test_calc_technical_exposes_expected_fields(self):
        technical = stock_service._calc_technical(self.df)
        self.assertEqual(technical["trend"], "Bullish")
        self.assertGreater(technical["vol_ratio"], 0.0)
        self.assertIn("atr_rr", technical)
        self.assertIn("candlestick_patterns", technical)


class RiskEngineEdgeCaseTests(unittest.TestCase):
    def setUp(self):
        self.engine = RiskEngine()

    def test_stress_engine_normalizes_shared_sector_labels(self):
        result = self.engine._stress_sync([{"ticker": "BBCA", "weight": 1.0, "sector": "Property"}])
        custom = next(item for item in result if item.scenario == "custom_minus30pct")
        self.assertEqual(custom.impact_pct, -40.0)
        self.assertEqual(custom.worst_ticker, "BBCA")

    def test_sector_alias_normalization_maps_shared_labels(self):
        self.assertEqual(_normalize_sector_label("Metals Mining"), "Metal Mining")
        self.assertEqual(_normalize_sector_label("Tech"), "Technology")

    def test_correlation_flags_highly_correlated_pair(self):
        base = pd.Series([0.01, 0.02, -0.01, 0.03, 0.01, 0.02])
        corr = self.engine._corr_sync({"BBCA": base, "BBRI": base * 1.01})
        self.assertTrue(corr.high_corr_pairs)
        self.assertTrue(corr.concentration)

    def test_var_returns_zero_for_short_series(self):
        ret = pd.Series([0.01, -0.02, 0.01])
        result = self.engine._var_sync("BBCA", ret, "historical")
        self.assertEqual(result.var_1d_95, 0)
        self.assertEqual(result.ann_vol, 0)


class CorruptFileRecoveryTests(unittest.TestCase):
    def test_picks_tracker_recovers_from_corrupt_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            history_file = Path(tmpdir) / "daily_picks_history.json"
            history_file.write_text("{bad json", encoding="utf-8")
            with patch.object(picks_tracker, "PICKS_HISTORY_FILE", str(history_file)):
                tracker = picks_tracker.PicksTracker()
                self.assertEqual(tracker._load(), [])

    def test_vector_memory_recovers_from_corrupt_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "store.json"
            json_path.write_text("not-json", encoding="utf-8")
            vm = vector_memory.VectorMemory()
            vm.JSON_PATH = str(json_path)
            vm._load_json()
            self.assertEqual(vm._json, [])


if __name__ == "__main__":
    unittest.main()
