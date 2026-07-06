from __future__ import annotations

import unittest

from utils import data_fetcher, stock_service
from utils.command_limits import format_retry_after


class DataFetcherFacadeTests(unittest.TestCase):
    def test_stock_data_uses_single_source(self):
        self.assertIs(data_fetcher.get_stock_data, stock_service.get_stock_data)

    def test_technical_helper_uses_single_source(self):
        self.assertIs(data_fetcher._calc_technical, stock_service._calc_technical)
        self.assertIs(data_fetcher._get_yfinance_data, stock_service._get_yfinance_data)


class CooldownFormattingTests(unittest.TestCase):
    def test_format_retry_after_seconds(self):
        self.assertEqual(format_retry_after(9.2), "9s")

    def test_format_retry_after_minutes(self):
        self.assertEqual(format_retry_after(61), "1m 1s")


if __name__ == "__main__":
    unittest.main()
