# memory/simple_memory.py — v3
# UPGRADES:
#   - save_analysis terima field `extra` (confidence, entry_quality, sector)
#   - build_context lebih informatif
#   - Regime context bisa disertakan

import json
import os
import threading
from datetime import datetime, timedelta
from typing import Optional

MEMORY_FILE = os.path.join(os.path.dirname(__file__), "memory_store.json")
MAX_HISTORY_PER_TICKER = 5
MAX_TICKER_AGE_DAYS = 30


class AnalysisMemory:
    def __init__(self):
        self._store: dict = {}
        self._lock  = threading.Lock()
        self._load_json()

    def save_analysis(self, ticker: str, analysis: str, recommendation: str,
                      score: float = 0.0, action: str = "HOLD",
                      extra: dict = None) -> None:
        key = ticker.upper()
        doc = {
            "ticker":         key,
            "analysis":       analysis[:500],
            "recommendation": recommendation[:200],
            "score":          score,
            "action":         action,
            "timestamp":      datetime.now().isoformat(),
            "date":           datetime.now().strftime("%d %b %Y"),
        }
        if extra:
            doc.update({
                "confidence":    extra.get("confidence", ""),
                "entry_quality": extra.get("entry_quality", ""),
                "sector":        extra.get("sector", ""),
            })

        with self._lock:
            if key not in self._store:
                self._store[key] = []
            self._store[key].append(doc)
            self._prune_locked()
            self._save_json()

    def get_history(self, ticker: str) -> list:
        with self._lock:
            return list(self._store.get(ticker.upper(), []))

    def get_last(self, ticker: str) -> Optional[dict]:
        hist = self.get_history(ticker)
        return hist[-1] if hist else None

    def build_context(self, ticker: str) -> str:
        hist = self.get_history(ticker)
        if not hist:
            return ""
        lines = [f"📜 **Riwayat analisis {ticker.upper()} (memory bot):**"]
        for h in hist[-3:]:
            conf = f" | Conf: {h['confidence']}" if h.get("confidence") else ""
            eq   = f" | Entry: {h['entry_quality']}" if h.get("entry_quality") else ""
            sec  = f" | Sektor: {h['sector']}" if h.get("sector") else ""
            lines.append(
                f"• {h['date']}: {h['action']} | Score: {h['score']:.1f}{conf}{eq}{sec}"
                f"\n  → {h['recommendation'][:100]}..."
            )
        return "\n".join(lines)

    def get_all_tickers(self) -> list:
        with self._lock:
            return list(self._store.keys())

    def _load_json(self):
        if os.path.exists(MEMORY_FILE):
            try:
                with open(MEMORY_FILE, "r") as f:
                    self._store = json.load(f)
                self._prune_locked()
            except Exception:
                self._store = {}

    def _prune_locked(self):
        cutoff = datetime.now() - timedelta(days=MAX_TICKER_AGE_DAYS)
        cleaned = {}
        for key, rows in self._store.items():
            if not isinstance(rows, list):
                continue
            valid_rows = []
            for row in rows:
                try:
                    ts = datetime.fromisoformat(row.get("timestamp", ""))
                except Exception:
                    continue
                if ts >= cutoff:
                    valid_rows.append(row)
            if valid_rows:
                cleaned[key] = valid_rows[-MAX_HISTORY_PER_TICKER:]
        self._store = cleaned

    def _save_json(self):
        try:
            os.makedirs(os.path.dirname(MEMORY_FILE), exist_ok=True)
            with open(MEMORY_FILE, "w") as f:
                json.dump(self._store, f, indent=2, default=str)
        except Exception as e:
            print(f"[Memory] ⚠️ Gagal simpan: {e}")


_memory = AnalysisMemory()

def get_memory() -> AnalysisMemory:
    return _memory
