from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import backtest.engine_backtest as engine


class WalkForwardEngineTests(unittest.TestCase):
    def test_optimizer_prefers_higher_scoring_params(self):
        wf = engine.WalkForwardEngine()
        df = pd.DataFrame(index=pd.date_range("2024-01-01", periods=120, freq="D"))

        def fake_run(self, df, ticker=""):
            score_bonus = (
                50.0
                if self.params.sl_mult == 1.5
                and self.params.tp1_mult == 3.0
                and self.params.entry_mode == "all"
                else 0.0
            )
            return engine.PeriodResult(
                label="",
                start="2024-01-01",
                end="2024-04-30",
                oos=False,
                total_trades=5,
                win_rate=55.0,
                pf=1.4 + score_bonus,
                total_return=12.0,
                max_dd=-5.0,
                sharpe=1.1,
                avg_win=3.0,
                avg_loss=1.5,
                params=self.params.as_dict(),
                trades=[],
            )

        with patch.object(engine.SingleBT, "run", fake_run):
            params, result = wf._optimize_params(df, "BBCA")

        self.assertEqual(params.sl_mult, 1.5)
        self.assertEqual(params.tp1_mult, 3.0)
        self.assertEqual(params.entry_mode, "all")
        self.assertEqual(result.params["sl_mult"], 1.5)

    def test_run_backtest_v4_uses_requested_parameters(self):
        idx = pd.date_range("2024-01-01", periods=90, freq="D")
        df = pd.DataFrame(
            {
                "Open": [100.0] * len(idx),
                "High": [101.0] * len(idx),
                "Low": [99.0] * len(idx),
                "Close": [100.0] * len(idx),
                "Volume": [1_000_000.0] * len(idx),
            },
            index=idx,
        )
        captured = {}

        def fake_run(self, df, ticker=""):
            captured["params"] = self.params.as_dict()
            return engine.PeriodResult(
                label="",
                start="2024-01-01",
                end="2024-03-31",
                oos=False,
                total_trades=0,
                win_rate=0.0,
                pf=0.0,
                total_return=0.0,
                max_dd=0.0,
                sharpe=0.0,
                avg_win=0.0,
                avg_loss=0.0,
                params=self.params.as_dict(),
                trades=[],
            )

        with patch.object(engine, "get_history", return_value=df), patch.object(engine.SingleBT, "run", fake_run):
            result = asyncio.run(engine.run_backtest_v4("BBCA", months=3, sl_mult=1.7, tp_mult=2.8))

        self.assertEqual(captured["params"]["sl_mult"], 1.7)
        self.assertEqual(captured["params"]["tp1_mult"], 2.8)
        self.assertEqual(captured["params"]["entry_mode"], "all")
        self.assertEqual(result["sl_mult"], 1.7)
        self.assertEqual(result["tp_mult"], 2.8)

    def test_monte_carlo_uses_adaptive_block_size(self):
        mc = engine.MonteCarloEngine()
        rng = np.random.default_rng(42)
        low_auto = rng.normal(0, 0.01, 80)
        high_auto = []
        current = 0.0
        for shock in rng.normal(0, 0.01, 80):
            current = 0.85 * current + shock
            high_auto.append(current)
        high_auto = np.array(high_auto)

        low_block = mc._choose_block_size(low_auto)
        high_block = mc._choose_block_size(high_auto)

        self.assertGreaterEqual(high_block, low_block)
        self.assertGreaterEqual(high_block, 3)

    def test_regime_filter_blocks_entries_in_bear_mode(self):
        idx = pd.date_range("2024-01-01", periods=90, freq="D")
        close = pd.Series(np.linspace(100, 120, len(idx)), index=idx)
        df = pd.DataFrame(
            {
                "Open": close - 1,
                "High": close + 2,
                "Low": close - 2,
                "Close": close,
                "Volume": [1_500_000.0] * len(idx),
            },
            index=idx,
        )
        bt = engine.SingleBT()

        with patch.object(engine.SingleBT, "_infer_regime", return_value="BEAR"), \
             patch.object(engine.SingleBT, "_check_entries", return_value=[("MOMENTUM", 110.0, 4)]):
            result = bt.run(df, ticker="BBCA")

        self.assertEqual(result.total_trades, 0)

    def test_kelly_throttles_reported_risk_pct(self):
        idx = pd.date_range("2024-01-01", periods=90, freq="D")
        df = pd.DataFrame(
            {
                "Open": [100.0] * len(idx),
                "High": [101.0] * len(idx),
                "Low": [99.0] * len(idx),
                "Close": [100.0] * len(idx),
                "Volume": [1_000_000.0] * len(idx),
            },
            index=idx,
        )

        fake_trades = [
            engine.Trade("BBCA", "2024-01-01", "2024-01-05", 100.0, 101.0, 1.0, 4, "TP2", 3),
            engine.Trade("BBCA", "2024-01-06", "2024-01-10", 100.0, 99.0, -1.0, 4, "SL", 3),
        ]

        def fake_run(self, df, ticker=""):
            return engine.PeriodResult(
                label="",
                start="2024-01-01",
                end="2024-03-31",
                oos=False,
                total_trades=2,
                win_rate=50.0,
                pf=1.0,
                total_return=1.0,
                max_dd=-2.0,
                sharpe=0.5,
                avg_win=1.0,
                avg_loss=1.0,
                params=self.params.as_dict(),
                trades=fake_trades,
            )

        with patch.object(engine, "get_history", return_value=df), \
             patch.object(engine.SingleBT, "run", fake_run), \
             patch.object(engine, "adaptive_risk_pct", return_value=0.02), \
             patch.object(engine, "kelly_aggressive", return_value=0.01):
            result = asyncio.run(engine.run_backtest_v4("BBCA", months=3))

        self.assertEqual(result["risk_per_trade_pct"], 1.0)


if __name__ == "__main__":
    unittest.main()
