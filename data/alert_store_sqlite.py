from __future__ import annotations

"""
alert_store_sqlite.py – SQLite-backed replacement for data/alert_store.py.

Drop-in replacement: public API is identical.  JSON fallback is kept for
the first startup so existing alerts.json data is migrated automatically.

Schema
------
alerts(
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    ticker      TEXT    NOT NULL,
    condition   TEXT    NOT NULL,   -- '>' or '<'
    price       REAL    NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL,
    triggered_at TEXT
)

Retention policy (enforced on every write):
  - Max MAX_ALERTS_PER_USER active alerts per user (oldest removed first).
  - Triggered alerts older than RETENTION_DAYS are purged.
"""

import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import List, Optional

ALERTS_DB = os.path.join(os.path.dirname(__file__), "alerts.db")
LEGACY_JSON = os.path.join(os.path.dirname(__file__), "alerts.json")
CACHE_DATE_DB_KEY = "picks_cache_date"

MAX_ALERTS_PER_USER: int = 20
RETENTION_DAYS: int = 90

_DDL = """
CREATE TABLE IF NOT EXISTS alerts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    ticker       TEXT    NOT NULL,
    condition    TEXT    NOT NULL,
    price        REAL    NOT NULL,
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT    NOT NULL,
    triggered_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_user ON alerts(user_id);
CREATE INDEX IF NOT EXISTS idx_alerts_active ON alerts(active);

CREATE TABLE IF NOT EXISTS kv_store (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class AlertStoreSQLite:
    """Thread-safe, SQLite-backed alert store."""

    def __init__(self, db_path: str = ALERTS_DB) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()
        self._migrate_from_json()

    # ── Init ──────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._conn() as conn:
            conn.executescript(_DDL)

    def _migrate_from_json(self) -> None:
        """One-shot migration from legacy alerts.json."""
        if not os.path.exists(LEGACY_JSON):
            return
        try:
            from utils.json_store import read_json

            data: dict = read_json(LEGACY_JSON, {})
            if not isinstance(data, dict) or not data:
                return
            with self._lock, self._conn() as conn:
                for record in data.values():
                    if not isinstance(record, dict):
                        continue
                    if not record.get("active"):
                        continue
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO alerts
                            (user_id, ticker, condition, price, active, created_at)
                        VALUES (?,?,?,?,1,?)
                        """,
                        (
                            int(record["user_id"]),
                            str(record["ticker"]).upper(),
                            str(record["condition"]),
                            float(record["price"]),
                            _utcnow(),
                        ),
                    )
            # Rename the old file so migration doesn't run again
            os.rename(LEGACY_JSON, LEGACY_JSON + ".migrated")
        except Exception as exc:
            print(f"[AlertStore] ⚠️ JSON migration skipped: {exc}")

    # ── Retention helpers ─────────────────────────────────

    def _enforce_retention(self, conn: sqlite3.Connection) -> None:
        """Purge triggered alerts older than RETENTION_DAYS."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
        ).isoformat()
        conn.execute(
            "DELETE FROM alerts WHERE active=0 AND triggered_at < ?", (cutoff,)
        )

    def _enforce_per_user_cap(
        self, conn: sqlite3.Connection, user_id: int
    ) -> None:
        """Keep at most MAX_ALERTS_PER_USER active alerts per user."""
        rows = conn.execute(
            "SELECT id FROM alerts WHERE user_id=? AND active=1 ORDER BY id ASC",
            (user_id,),
        ).fetchall()
        excess = len(rows) - MAX_ALERTS_PER_USER
        if excess > 0:
            ids_to_drop = [row["id"] for row in rows[:excess]]
            conn.execute(
                f"DELETE FROM alerts WHERE id IN ({','.join('?'*len(ids_to_drop))})",
                ids_to_drop,
            )

    # ── Public API ────────────────────────────────────────

    def add(
        self, user_id: int, ticker: str, condition: str, price: float
    ) -> bool:
        """
        Add an alert.  Returns True on success, False if a duplicate exists.
        """
        ticker = ticker.upper()
        with self._lock, self._conn() as conn:
            existing = conn.execute(
                """
                SELECT id FROM alerts
                WHERE user_id=? AND ticker=? AND condition=? AND price=? AND active=1
                """,
                (user_id, ticker, condition, price),
            ).fetchone()
            if existing:
                return False
            conn.execute(
                """
                INSERT INTO alerts (user_id, ticker, condition, price, active, created_at)
                VALUES (?,?,?,?,1,?)
                """,
                (user_id, ticker, condition, price, _utcnow()),
            )
            self._enforce_per_user_cap(conn, user_id)
            self._enforce_retention(conn)
        return True

    def remove(self, user_id: int, ticker: str) -> int:
        """
        Permanently delete all active alerts for (user, ticker).
        Returns count of removed rows.
        """
        ticker = ticker.upper()
        with self._lock, self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM alerts WHERE user_id=? AND ticker=? AND active=1",
                (user_id, ticker),
            )
            return cursor.rowcount

    def get_user_alerts(self, user_id: int) -> List[dict]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM alerts WHERE user_id=? AND active=1 ORDER BY id",
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_all_active(self) -> List[dict]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM alerts WHERE active=1"
            ).fetchall()
        return [dict(row) for row in rows]

    def deactivate(
        self, user_id: int, ticker: str, condition: str, price: float
    ) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                UPDATE alerts SET active=0, triggered_at=?
                WHERE user_id=? AND ticker=? AND condition=? AND price=? AND active=1
                """,
                (_utcnow(), user_id, ticker.upper(), condition, price),
            )


# ── Picks cache date (KV store) ───────────────────────────


class _PicksCacheDateStore:
    def __init__(self, db_path: str = ALERTS_DB) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def get(self) -> str:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key=?", (CACHE_DATE_DB_KEY,)
            ).fetchone()
        return row[0] if row else ""

    def set(self, date_str: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_store(key, value) VALUES (?,?)",
                (CACHE_DATE_DB_KEY, date_str),
            )


_picks_cache_store = _PicksCacheDateStore()
_store: Optional[AlertStoreSQLite] = None


def get_alert_store() -> AlertStoreSQLite:
    global _store
    if _store is None:
        _store = AlertStoreSQLite()
    return _store


# Backward-compatible module-level helpers
def get_picks_cache_date() -> str:
    return _picks_cache_store.get()


def set_picks_cache_date(date_str: str) -> None:
    _picks_cache_store.set(date_str)
