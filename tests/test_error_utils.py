from __future__ import annotations

import unittest

from utils.error_utils import user_error_message
from utils.yf_guard import YFinanceUnavailable


class ErrorUtilsTests(unittest.TestCase):
    def test_yfinance_error_is_preserved(self):
        exc = YFinanceUnavailable("yfinance sedang bermasalah")
        self.assertEqual(user_error_message(exc), "yfinance sedang bermasalah")

    def test_timeout_is_humanized(self):
        self.assertIn("timeout", user_error_message(TimeoutError()).lower())

    def test_empty_error_uses_fallback(self):
        self.assertEqual(
            user_error_message(RuntimeError(""), fallback="fallback"),
            "fallback",
        )


if __name__ == "__main__":
    unittest.main()
