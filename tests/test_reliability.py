from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from memory import simple_memory
from utils import yf_guard


class YFinanceGuardTests(unittest.TestCase):
    def setUp(self):
        yf_guard._state["failures"] = 0
        yf_guard._state["opened_until"] = 0.0
        yf_guard._state["last_error"] = ""
        yf_guard._cache.clear()

    def test_circuit_breaker_opens_after_repeated_failures(self):
        for _ in range(yf_guard.FAILURE_THRESHOLD):
            with self.assertRaises(RuntimeError):
                yf_guard._run(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        self.assertTrue(yf_guard.get_status()["open"])

    def test_circuit_breaker_blocks_while_open(self):
        yf_guard._state["opened_until"] = 10**9
        with self.assertRaises(yf_guard.YFinanceUnavailable):
            yf_guard._before_call()

    def test_run_cached_reuses_recent_result(self):
        calls = {"count": 0}

        def _fn():
            calls["count"] += 1
            return {"value": 42}

        key = yf_guard._cache_key("info", "BBCA.JK")
        first = yf_guard._run_cached(key, 60.0, _fn)
        second = yf_guard._run_cached(key, 60.0, _fn)

        self.assertEqual(calls["count"], 1)
        self.assertEqual(first, second)


class SimpleMemoryTests(unittest.TestCase):
    def test_prune_keeps_recent_rows_only(self):
        old_ts = (datetime.now() - timedelta(days=simple_memory.MAX_TICKER_AGE_DAYS + 5)).isoformat()
        new_ts = datetime.now().isoformat()
        store = simple_memory.AnalysisMemory.__new__(simple_memory.AnalysisMemory)
        store._store = {
            "BBCA": [
                {"timestamp": old_ts, "recommendation": "old"},
                {"timestamp": new_ts, "recommendation": "new"},
            ],
        }
        store._prune_locked()
        self.assertEqual(len(store._store["BBCA"]), 1)
        self.assertEqual(store._store["BBCA"][0]["recommendation"], "new")

    def test_load_json_prunes_old_entries_from_disk(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_file = Path(tmpdir) / "memory_store.json"
            old_ts = (datetime.now() - timedelta(days=simple_memory.MAX_TICKER_AGE_DAYS + 5)).isoformat()
            payload = {"BBCA": [{"timestamp": old_ts, "recommendation": "old"}]}
            memory_file.write_text(json.dumps(payload), encoding="utf-8")

            with patch.object(simple_memory, "MEMORY_FILE", str(memory_file)):
                store = simple_memory.AnalysisMemory()

            self.assertEqual(store.get_all_tickers(), [])


if __name__ == "__main__":
    unittest.main()
