from __future__ import annotations

import unittest

import pandas as pd

from risk.optimizer import PortfolioOptimizer


class PortfolioOptimizerTests(unittest.TestCase):
    def setUp(self):
        self.optimizer = PortfolioOptimizer()
        self.returns_df = pd.DataFrame(
            {
                "BBCA": [0.01, 0.02, -0.01, 0.015, 0.005, -0.002],
                "BBRI": [0.008, 0.018, -0.012, 0.017, 0.004, -0.001],
            }
        )

    def test_build_generates_rebalance_trades_when_delta_large(self):
        result = self.optimizer._build(
            tickers=["BBCA", "BBRI"],
            weights_dict={"BBCA": 0.7, "BBRI": 0.3},
            returns_df=self.returns_df,
            current_weights={"BBCA": 0.4, "BBRI": 0.6},
            method="mean_variance",
        )

        self.assertEqual(len(result.rebalance_trades), 2)
        self.assertIn("BUY", {trade["action"] for trade in result.rebalance_trades})
        self.assertIn("SELL", {trade["action"] for trade in result.rebalance_trades})

    def test_equal_weight_adds_fallback_note(self):
        result = self.optimizer._equal_weight(["BBCA", "BBRI"], self.returns_df, current_weights=None)
        self.assertEqual(result.method, "equal_weight")
        self.assertTrue(any("Optimizer library" in note for note in result.notes))

    def test_kelly_size_never_negative(self):
        result = self.optimizer.kelly_size(win_rate=40.0, avg_win=2.0, avg_loss=4.0)
        self.assertGreaterEqual(result["position_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()
