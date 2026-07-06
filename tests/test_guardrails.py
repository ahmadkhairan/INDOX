from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from fastapi import HTTPException

import api.app as api_app
from core.ai_engine import _run_async
from utils.ticker_utils import normalize_ticker, normalize_tickers


class TickerValidationTests(unittest.TestCase):
    def test_normalize_ticker_accepts_valid_idx_code(self):
        self.assertEqual(normalize_ticker(" bbca "), "BBCA")

    def test_normalize_ticker_rejects_injection_like_input(self):
        with self.assertRaises(ValueError):
            normalize_ticker("BBCA; DROP TABLE")

    def test_normalize_tickers_rejects_invalid_member(self):
        with self.assertRaises(ValueError):
            normalize_tickers(["BBCA", "TLKM!", "ADRO"])


class ApiGuardTests(unittest.TestCase):
    def test_auth_rejects_missing_or_weak_secret(self):
        with patch.object(api_app, "API_SECRET", "changeme"):
            with self.assertRaises(HTTPException) as ctx:
                api_app.auth("changeme")
        self.assertEqual(ctx.exception.status_code, 503)

    def test_auth_accepts_matching_secret(self):
        with patch.object(api_app, "API_SECRET", "super-secret"):
            self.assertTrue(api_app.auth("super-secret"))


class AsyncWrapperTests(unittest.TestCase):
    def test_run_async_executes_without_existing_loop(self):
        result = _run_async(asyncio.sleep(0, result="ok"))
        self.assertEqual(result, "ok")

    def test_run_async_fails_fast_inside_running_loop(self):
        async def _call_wrapper():
            return _run_async(asyncio.sleep(0, result="ok"))

        result = asyncio.run(_call_wrapper())
        self.assertIn("event loop aktif", result)


if __name__ == "__main__":
    unittest.main()
