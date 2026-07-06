from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import yfinance as yf


FAILURE_THRESHOLD = 3
OPEN_SECONDS = 90.0
MAX_CACHE_ENTRIES = 256

TTL_INFO = 20.0
TTL_FAST_INFO = 10.0
TTL_HISTORY = 20.0
TTL_NEWS = 60.0
TTL_HOLDERS = 300.0
TTL_DOWNLOAD = 15.0

_state = {
    "failures": 0,
    "opened_until": 0.0,
    "last_error": "",
}
_lock = threading.Lock()
_cache_lock = threading.Lock()
_cache: dict[tuple, tuple[Any, float]] = {}


class YFinanceUnavailable(RuntimeError):
    pass


def _retry_after() -> int:
    remaining = int(max(1, round(_state["opened_until"] - time.monotonic())))
    return remaining


def _clone(value: Any) -> Any:
    if hasattr(value, "copy"):
        try:
            return value.copy(deep=True)
        except TypeError:
            try:
                return value.copy()
            except Exception:
                return value
    return value


def _cache_key(name: str, symbol: Any, kwargs: dict[str, Any] | None = None) -> tuple:
    parts = [name, symbol]
    if kwargs:
        parts.extend(sorted(kwargs.items()))
    return tuple(parts)


def _cache_get(key: tuple) -> Any | None:
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at <= time.monotonic():
            _cache.pop(key, None)
            return None
        return _clone(value)


def _cache_set(key: tuple, value: Any, ttl: float) -> Any:
    with _cache_lock:
        if len(_cache) >= MAX_CACHE_ENTRIES:
            expired = [k for k, (_, expires_at) in _cache.items() if expires_at <= time.monotonic()]
            for item in expired:
                _cache.pop(item, None)
            if len(_cache) >= MAX_CACHE_ENTRIES and _cache:
                oldest_key = min(_cache.items(), key=lambda item: item[1][1])[0]
                _cache.pop(oldest_key, None)
        _cache[key] = (_clone(value), time.monotonic() + ttl)
    return value


def _before_call() -> None:
    with _lock:
        opened_until = _state["opened_until"]
        last_error = _state["last_error"]
    if opened_until > time.monotonic():
        msg = f"yfinance sedang bermasalah. Coba lagi dalam {_retry_after()} detik."
        if last_error:
            msg = f"{msg} Error terakhir: {last_error}"
        raise YFinanceUnavailable(msg)


def _on_success() -> None:
    with _lock:
        _state["failures"] = 0
        _state["opened_until"] = 0.0
        _state["last_error"] = ""


def _on_failure(exc: Exception) -> None:
    err = str(exc).strip() or exc.__class__.__name__
    with _lock:
        _state["failures"] += 1
        _state["last_error"] = err
        if _state["failures"] >= FAILURE_THRESHOLD:
            _state["opened_until"] = time.monotonic() + OPEN_SECONDS


def _run(fn: Callable[[], Any]) -> Any:
    _before_call()
    try:
        result = fn()
    except YFinanceUnavailable:
        raise
    except Exception as exc:
        _on_failure(exc)
        raise
    _on_success()
    return result


def _run_cached(key: tuple, ttl: float, fn: Callable[[], Any]) -> Any:
    cached = _cache_get(key)
    if cached is not None:
        return cached
    result = _run(fn)
    return _cache_set(key, result, ttl)


def get_ticker(symbol: str):
    _before_call()
    return yf.Ticker(symbol)


def get_info(symbol: str) -> dict:
    key = _cache_key("info", symbol)
    return _run_cached(key, TTL_INFO, lambda: get_ticker(symbol).info)


def get_fast_info(symbol: str):
    key = _cache_key("fast_info", symbol)
    return _run_cached(key, TTL_FAST_INFO, lambda: get_ticker(symbol).fast_info)


def get_history(symbol: str, **kwargs):
    key = _cache_key("history", symbol, kwargs)
    return _run_cached(key, TTL_HISTORY, lambda: get_ticker(symbol).history(**kwargs))


def get_news(symbol: str):
    key = _cache_key("news", symbol)
    return _run_cached(key, TTL_NEWS, lambda: get_ticker(symbol).news or [])


def get_institutional_holders(symbol: str):
    key = _cache_key("holders", symbol)
    return _run_cached(key, TTL_HOLDERS, lambda: get_ticker(symbol).institutional_holders)


def download(symbols: list[str] | tuple[str, ...], **kwargs):
    symbol_key = tuple(symbols)
    key = _cache_key("download", symbol_key, kwargs)
    return _run_cached(key, TTL_DOWNLOAD, lambda: yf.download(list(symbols), **kwargs))


def get_status() -> dict:
    with _lock:
        opened_until = _state["opened_until"]
        return {
            "failures": _state["failures"],
            "open": opened_until > time.monotonic(),
            "retry_after": max(0, int(round(opened_until - time.monotonic()))),
            "last_error": _state["last_error"],
        }
