import asyncio
import unittest
import pandas as pd
import numpy as np

from utils.microstructure import (
    get_bei_tick_size,
    round_to_tick,
    calc_lot_value,
    get_ara_limit,
    get_arb_limit,
)
from execution.paper_trader import PaperTrader
from backtest.engine_backtest import SingleBT
from core.http_session import get_shared_session, close_shared_session
from pathlib import Path
import tempfile
import shutil


class TestBEIMicrostructure(unittest.TestCase):
    def test_get_bei_tick_size_tiers(self):
        # Tier 1: < 200 -> 1
        self.assertEqual(get_bei_tick_size(50), 1)
        self.assertEqual(get_bei_tick_size(199), 1)

        # Tier 2: 200 - < 500 -> 2
        self.assertEqual(get_bei_tick_size(200), 2)
        self.assertEqual(get_bei_tick_size(498), 2)

        # Tier 3: 500 - < 2000 -> 5
        self.assertEqual(get_bei_tick_size(500), 5)
        self.assertEqual(get_bei_tick_size(1995), 5)

        # Tier 4: 2000 - < 5000 -> 10
        self.assertEqual(get_bei_tick_size(2000), 10)
        self.assertEqual(get_bei_tick_size(4990), 10)

        # Tier 5: >= 5000 -> 25
        self.assertEqual(get_bei_tick_size(5000), 25)
        self.assertEqual(get_bei_tick_size(9525), 25)
        self.assertEqual(get_bei_tick_size(25000), 25)

    def test_round_to_tick_nearest(self):
        self.assertEqual(round_to_tick(150.4, "nearest"), 150.0)
        self.assertEqual(round_to_tick(150.6, "nearest"), 151.0)
        self.assertEqual(round_to_tick(351.4, "nearest"), 352.0)
        self.assertEqual(round_to_tick(1234.0, "nearest"), 1235.0)
        self.assertEqual(round_to_tick(3454.0, "nearest"), 3450.0)
        self.assertEqual(round_to_tick(3456.0, "nearest"), 3460.0)
        self.assertEqual(round_to_tick(9534.2, "nearest"), 9525.0)
        self.assertEqual(round_to_tick(9540.0, "nearest"), 9550.0)

    def test_round_to_tick_floor_and_ceil(self):
        # Floor (Stop Loss conservative)
        self.assertEqual(round_to_tick(9545.0, "floor"), 9525.0)
        self.assertEqual(round_to_tick(1234.0, "floor"), 1230.0)
        self.assertEqual(round_to_tick(351.0, "floor"), 350.0)

        # Ceil (Take Profit)
        self.assertEqual(round_to_tick(9530.0, "ceil"), 9550.0)
        self.assertEqual(round_to_tick(1231.0, "ceil"), 1235.0)
        self.assertEqual(round_to_tick(351.0, "ceil"), 352.0)

    def test_calc_lot_value(self):
        # 1 lot = 100 shares
        self.assertEqual(calc_lot_value(1000, 10), 1_000_000.0)
        self.assertEqual(calc_lot_value(9500, 5), 4_750_000.0)
        self.assertEqual(calc_lot_value(0, 10), 0.0)

    def test_ara_arb_limits(self):
        # Tier 5 (>=5000): 20% limit
        ara = get_ara_limit(10000)
        self.assertEqual(ara, 12000.0)
        arb = get_arb_limit(10000)
        self.assertEqual(arb, 8000.0)

        # Tier 1 (<200): 35% limit
        ara_t1 = get_ara_limit(100)
        self.assertEqual(ara_t1, 135.0)
        arb_t1 = get_arb_limit(100)
        self.assertEqual(arb_t1, 65.0)


class TestPaperTradingMicrostructure(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.pt = PaperTrader(data_dir=self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_enter_rounds_to_valid_bei_ticks(self):
        trade_id = self.pt.enter(
            ticker="BBCA",
            entry_price=9534.2,  # Should round to 9525
            sl=9211.3,          # Should floor to 9200
            tp1=9844.7,         # Should ceil to 9850
            tp2=10123.9,        # Should ceil to 10125
            qty_lots=10,
        )
        trade = self.pt.get_trade(trade_id)
        self.assertIsNotNone(trade)
        self.assertEqual(trade.entry_price, 9525.0)
        self.assertEqual(trade.sl, 9200.0)
        self.assertEqual(trade.tp1, 9850.0)
        self.assertEqual(trade.tp2, 10125.0)

        # Check tick size validity
        self.assertEqual(trade.entry_price % get_bei_tick_size(trade.entry_price), 0)
        self.assertEqual(trade.sl % get_bei_tick_size(trade.sl), 0)
        self.assertEqual(trade.tp1 % get_bei_tick_size(trade.tp1), 0)
        self.assertEqual(trade.tp2 % get_bei_tick_size(trade.tp2), 0)

    def test_check_single_tp1_breakeven_tick_aligned(self):
        trade_id = self.pt.enter(
            ticker="BBCA",
            entry_price=9500,
            sl=9200,
            tp1=9800,
            tp2=10100,
            qty_lots=10,
        )
        trade = self.pt.get_trade(trade_id)
        # Price hits TP1
        reason = self.pt._check_single(trade, 9800)
        self.assertEqual(reason, "TP1")
        self.assertEqual(trade.status, "TP1_HIT")
        # SL should be raised to entry * 1.002 = 9519 -> floor tick = 9500
        self.assertEqual(trade.sl % get_bei_tick_size(trade.sl), 0)
        self.assertTrue(trade.sl >= 9500.0)


class TestBacktestMicrostructure(unittest.TestCase):
    def test_backtest_trade_prices_are_valid_ticks(self):
        # Generate dummy 100 days of price data
        np.random.seed(42)
        dates = pd.date_range(start="2025-01-01", periods=120, freq="B")
        close = np.linspace(8000, 10500, 120) + np.random.normal(0, 150, 120)
        high = close + np.random.uniform(50, 200, 120)
        low = close - np.random.uniform(50, 200, 120)
        volume = np.random.uniform(10_000_000, 50_000_000, 120)

        df = pd.DataFrame({
            "Open": close - 10,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume,
        }, index=dates)

        bt = SingleBT()
        res = bt.run(df, ticker="BBCA")
        for trade in res.trades:
            # Check entry price tick
            tick = get_bei_tick_size(trade.entry_price)
            self.assertEqual(
                trade.entry_price % tick,
                0,
                f"Entry price {trade.entry_price} not divisible by tick {tick}",
            )


class TestHTTPSessionLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_session_lifecycle(self):
        session1 = await get_shared_session()
        self.assertFalse(session1.closed)

        session2 = await get_shared_session()
        self.assertIs(session1, session2)

        await close_shared_session()
        self.assertTrue(session1.closed)

        # Recreating after close should return a new session
        session3 = await get_shared_session()
        self.assertIsNot(session1, session3)
        self.assertFalse(session3.closed)

        await close_shared_session()
        self.assertTrue(session3.closed)


if __name__ == "__main__":
    unittest.main()
