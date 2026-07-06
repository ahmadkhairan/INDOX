from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta

from utils.json_store import read_json, write_json
from utils.yf_guard import YFinanceUnavailable, get_history


PICKS_HISTORY_FILE = os.path.join(os.path.dirname(__file__), "daily_picks_history.json")


class PicksTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()

    def _load(self) -> list[dict]:
        data = read_json(PICKS_HISTORY_FILE, [])
        return data if isinstance(data, list) else []

    def _save(self, rows: list[dict]) -> None:
        write_json(PICKS_HISTORY_FILE, rows, ensure_ascii=False, indent=2)

    def record_run(self, pick_date: str, selected: list[dict], shortlist: list[dict]) -> None:
        if not selected:
            return
        shortlist_brief = [
            {
                "ticker": item.get("ticker"),
                "quant_rank": item.get("quant", {}).get("rank"),
                "quant_score": item.get("quant", {}).get("score"),
                "score": item.get("score", {}).get("total"),
            }
            for item in shortlist[:12]
        ]
        row = {
            "date": pick_date,
            "selected": selected,
            "shortlist": shortlist_brief,
            "created_at": datetime.utcnow().isoformat(),
        }

        with self._lock:
            rows = self._load()
            rows = [r for r in rows if r.get("date") != pick_date]
            rows.append(row)
            rows.sort(key=lambda r: r.get("date", ""))
            self._save(rows)

    def _fetch_reference_price(self, ticker: str, target_date: datetime) -> float | None:
        try:
            start = (target_date - timedelta(days=3)).strftime("%Y-%m-%d")
            end = (target_date + timedelta(days=4)).strftime("%Y-%m-%d")
            hist = get_history(f"{ticker}.JK", start=start, end=end, auto_adjust=True)
            if hist.empty:
                return None
            return round(float(hist["Close"].iloc[0]), 2)
        except YFinanceUnavailable:
            return None
        except Exception:
            return None

    def _fetch_forward_price(self, ticker: str, target_date: datetime) -> float | None:
        try:
            start = target_date.strftime("%Y-%m-%d")
            end = (target_date + timedelta(days=7)).strftime("%Y-%m-%d")
            hist = get_history(f"{ticker}.JK", start=start, end=end, auto_adjust=True)
            if hist.empty:
                return None
            return round(float(hist["Close"].iloc[-1]), 2)
        except YFinanceUnavailable:
            return None
        except Exception:
            return None

    def refresh_results(self) -> None:
        with self._lock:
            rows = self._load()
            changed = False
            for row in rows:
                try:
                    pick_dt = datetime.strptime(row.get("date", ""), "%Y-%m-%d")
                except Exception:
                    continue
                for item in row.get("selected", []):
                    if item.get("entry_price") is None:
                        item["entry_price"] = self._fetch_reference_price(item["ticker"], pick_dt)
                        changed = True
                    for horizon in (7, 14):
                        key = f"return_{horizon}d"
                        price_key = f"close_{horizon}d"
                        if item.get(key) is not None:
                            continue
                        target = pick_dt + timedelta(days=horizon)
                        if target > datetime.utcnow():
                            continue
                        future_price = self._fetch_forward_price(item["ticker"], target)
                        entry_price = item.get("entry_price")
                        if future_price is None or not entry_price:
                            continue
                        item[price_key] = future_price
                        item[key] = round((future_price / entry_price - 1.0) * 100.0, 2)
                        changed = True
            if changed:
                self._save(rows)

    def summarize(self, lookback_days: int = 30) -> dict:
        with self._lock:
            rows = self._load()

        cutoff = datetime.utcnow() - timedelta(days=lookback_days)
        selected = []
        for row in rows:
            try:
                row_dt = datetime.strptime(row.get("date", ""), "%Y-%m-%d")
            except Exception:
                continue
            if row_dt < cutoff:
                continue
            selected.extend(row.get("selected", []))

        def _metrics(field: str) -> dict:
            vals = [float(item[field]) for item in selected if item.get(field) is not None]
            if not vals:
                return {"count": 0, "hit_rate": None, "avg_return": None}
            wins = sum(1 for value in vals if value > 0)
            return {
                "count": len(vals),
                "hit_rate": round(wins / len(vals) * 100.0, 1),
                "avg_return": round(sum(vals) / len(vals), 2),
            }

        return {
            "days": lookback_days,
            "total_picks": len(selected),
            "ret_7d": _metrics("return_7d"),
            "ret_14d": _metrics("return_14d"),
            "last_dates": [row.get("date") for row in rows[-5:]],
        }


_tracker = PicksTracker()


def get_picks_tracker() -> PicksTracker:
    return _tracker
