from __future__ import annotations

"""
utils/runtime_cache.py — Thread-safe TTL cache with hit/miss counters.

Changes from original:
  - Tracks hits, misses, sets, and evictions.
  - get_stats() returns a snapshot dict suitable for /metrics.
  - A module-level registry allows the FastAPI /metrics endpoint to
    enumerate all active caches without importing each one individually.
"""

import copy
import threading
import time
from typing import Callable, Generic, TypeVar

T = TypeVar("T")

# Module-level registry so /metrics can aggregate across all caches.
_registry: dict[str, "TTLCache"] = {}
_registry_lock = threading.Lock()


def register_cache(name: str, cache: "TTLCache") -> "TTLCache":
    with _registry_lock:
        _registry[name] = cache
    return cache


def get_all_cache_stats() -> dict[str, dict]:
    with _registry_lock:
        names = list(_registry.keys())
    return {name: _registry[name].get_stats() for name in names}


class TTLCache(Generic[T]):
    def __init__(self, max_entries: int = 128, name: str = "") -> None:
        self._max_entries = max(1, max_entries)
        self._lock = threading.Lock()
        self._entries: dict[object, tuple[T, float]] = {}

        # Counters (never reset; monotonically increasing)
        self._hits = 0
        self._misses = 0
        self._sets = 0
        self._evictions = 0

        if name:
            register_cache(name, self)

    # ── Core ops ──────────────────────────────────────────

    def get(self, key: object) -> T | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None
            value, expires_at = entry
            if expires_at <= time.monotonic():
                self._entries.pop(key, None)
                self._misses += 1
                self._evictions += 1
                return None
            self._hits += 1
            return copy.deepcopy(value)

    def set(self, key: object, value: T, ttl: float) -> T:
        expires_at = time.monotonic() + max(ttl, 0.0)
        with self._lock:
            self._sets += 1
            self._prune_locked()
            self._entries[key] = (copy.deepcopy(value), expires_at)
            self._prune_locked()
        return copy.deepcopy(value)

    def get_or_set(self, key: object, ttl: float, builder: Callable[[], T]) -> T:
        cached = self.get(key)
        if cached is not None:
            return cached
        value = builder()
        return self.set(key, value, ttl)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    # ── Stats ─────────────────────────────────────────────

    def get_stats(self) -> dict:
        with self._lock:
            size = len(self._entries)
            hits = self._hits
            misses = self._misses
            sets = self._sets
            evictions = self._evictions
        total = hits + misses
        hit_rate = round(hits / total, 4) if total else 0.0
        return {
            "size": size,
            "max_entries": self._max_entries,
            "hits": hits,
            "misses": misses,
            "sets": sets,
            "evictions": evictions,
            "hit_rate": hit_rate,
        }

    # ── Internal ──────────────────────────────────────────

    def _prune_locked(self) -> None:
        now = time.monotonic()
        expired = [k for k, (_, exp) in self._entries.items() if exp <= now]
        for k in expired:
            self._entries.pop(k, None)
            self._evictions += 1
        while len(self._entries) > self._max_entries and self._entries:
            oldest = min(self._entries.items(), key=lambda item: item[1][1])[0]
            self._entries.pop(oldest, None)
            self._evictions += 1
