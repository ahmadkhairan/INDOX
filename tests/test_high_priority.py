from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import api.app as api_app
from data import alert_store
from utils.fundamental_utils import normalize_der
from utils.groq_utils import validate_groq_config
from utils.json_store import read_json, write_json
from utils import market_regime


class JsonStoreTests(unittest.TestCase):
    def test_write_and_read_json_atomically(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            payload = {"ticker": "BBCA", "active": True}
            write_json(str(path), payload, ensure_ascii=False, indent=2)

            self.assertEqual(read_json(str(path), {}), payload)

    def test_read_json_returns_default_on_corrupt_payload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            path.write_text("{broken", encoding="utf-8")
            self.assertEqual(read_json(str(path), {"safe": True}), {"safe": True})


class AlertStorePersistenceTests(unittest.TestCase):
    def test_alert_store_persists_with_atomic_helper(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            alert_file = Path(tmpdir) / "alerts.json"
            cache_file = Path(tmpdir) / "picks_cache_date.json"
            with patch.object(alert_store, "ALERT_FILE", str(alert_file)), patch.object(alert_store, "CACHE_DATE_FILE", str(cache_file)):
                store = alert_store.AlertStore()
                self.assertTrue(store.add(1, "BBCA", ">", 9000))
                reloaded = alert_store.AlertStore()
                alerts = reloaded.get_user_alerts(1)
                self.assertEqual(len(alerts), 1)
                self.assertEqual(alerts[0]["ticker"], "BBCA")
                alert_store.set_picks_cache_date("2026-04-05")
                self.assertEqual(alert_store.get_picks_cache_date(), "2026-04-05")


class GroqValidationTests(unittest.TestCase):
    def test_validate_groq_accepts_expected_prefix(self):
        ok, message = validate_groq_config(api_key="gsk_test_key_12345678901234567890", model="llama-test")
        self.assertTrue(ok)
        self.assertEqual(message, "ok")

    def test_validate_groq_rejects_bad_format(self):
        ok, message = validate_groq_config(api_key="wrong-key", model="llama-test")
        self.assertFalse(ok)
        self.assertIn("Format", message)


class FundamentalNormalizationTests(unittest.TestCase):
    def test_der_normalization_keeps_valid_banking_leverage(self):
        self.assertEqual(normalize_der(8.5, "Banking"), 8.5)

    def test_der_normalization_converts_percentage_like_value(self):
        self.assertEqual(normalize_der(171.0, "General"), 1.71)


class CoalFallbackTests(unittest.TestCase):
    def setUp(self):
        market_regime._coal_cache.update({"price": 0.0, "ts": 0, "source": "", "score_bonus": 0})

    def test_unavailable_coal_data_does_not_apply_bonus(self):
        with patch.object(market_regime, "get_fast_info", side_effect=RuntimeError("boom")), patch.object(
            market_regime, "get_history", side_effect=RuntimeError("boom")
        ):
            result = market_regime.get_coal_price()

        self.assertEqual(result["source"], "unavailable")
        self.assertEqual(result["score_bonus"], 0)
        self.assertFalse(result["rally"])


class ApiVersioningTests(unittest.TestCase):
    def test_versioned_routes_are_registered_under_v4_prefix(self):
        paths = {route.path for route in api_app.app.routes}
        self.assertIn("/api/v4/analyze", paths)
        self.assertIn("/api/v4/optimize", paths)
        self.assertIn("/api/v4/backtest/{ticker}", paths)


if __name__ == "__main__":
    unittest.main()
