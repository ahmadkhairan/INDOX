from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

import pandas as pd
from fastapi.testclient import TestClient

import api.app as api_app


class ApiBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.secret_patch = patch.object(api_app, "API_SECRET", "super-secret")
        self.secret_patch.start()
        self._client_cm = TestClient(api_app.app)
        self.client = self._client_cm.__enter__()

    def tearDown(self):
        self._client_cm.__exit__(None, None, None)
        self.secret_patch.stop()

    def test_health_sets_api_version_header(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-API-Version"), "v4")

    def test_analyze_requires_api_key(self):
        response = self.client.post("/api/v4/analyze", json={"ticker": "BBCA"})
        self.assertEqual(response.status_code, 401)

    def test_analyze_returns_fetcher_error_as_http_error(self):
        with patch("data.fetcher.fetch_ticker_data", new=AsyncMock(return_value={"error": "provider down"})), \
             patch("data.fetcher.fetch_news", new=AsyncMock(return_value=[])):
            response = self.client.post(
                "/api/v4/analyze",
                headers={"X-API-Key": "super-secret"},
                json={"ticker": "BBCA", "include_sentiment": False, "include_var": False},
            )

        self.assertEqual(response.status_code, 502)
        self.assertIn("provider down", response.text)

    def test_optimize_rejects_single_ticker(self):
        response = self.client.post(
            "/api/v4/optimize",
            headers={"X-API-Key": "super-secret"},
            json={"tickers": ["BBCA"], "method": "mean_variance"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Minimal 2 ticker", response.text)

    def test_optimize_returns_optimizer_payload(self):
        hist = pd.DataFrame({"Close": [100.0, 101.0, 102.0, 104.0]})

        class _Result:
            method = "mean_variance"
            weights = {"BBCA": 0.6, "BBRI": 0.4}
            expected_return = 12.3
            expected_vol = 10.5
            sharpe = 0.6
            cvar_95 = 4.2
            kelly_sizes = {"BBCA": 10.0, "BBRI": 8.0}
            rebalance_trades = []
            notes = ["ok"]

        optimizer = type("Optimizer", (), {"optimize": AsyncMock(return_value=_Result())})()

        with patch("utils.yf_guard.get_history", return_value=hist), patch("risk.optimizer.get_optimizer", return_value=optimizer):
            response = self.client.post(
                "/api/v4/optimize",
                headers={"X-API-Key": "super-secret"},
                json={"tickers": ["BBCA", "BBRI"], "method": "mean_variance"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["method"], "mean_variance")
        self.assertEqual(payload["weights"]["BBCA"], 0.6)


if __name__ == "__main__":
    unittest.main()
