"""IDX Official API integration — fallback / primary source for IDX data.

Provides:
- Daily trading summary (price, volume, value, freq)
- Foreign flow (net buy/sell)
- Historical EOD data (best-effort; yfinance fallback in stock_service)
- Listed companies & sector mapping

Uses idx.co.id/primary/ endpoints (umbraco/Surface deprecated).
Rate limiting: respect IDX servers (max 1 req/sec recommended).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

import aiohttp
import pandas as pd

from utils.logger import get_logger

log = get_logger("idx_api")

_CURL_CFFI = False
try:
    from curl_cffi import requests as _curl_requests
    _CURL_CFFI = True
except ImportError:
    pass


@dataclass
class DailySummary:
    ticker: str
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    value: float
    frequency: int
    change_pct: float
    foreign_buy: int = 0
    foreign_sell: int = 0
    foreign_net: int = 0
    source: str = "IDX Official"


@dataclass
class HistoricalBar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


class IDXRateLimit:
    """Simple rate limiter — max 1 request/second to respect IDX servers."""

    def __init__(self, min_interval: float = 1.0):
        self.min_interval = min_interval
        self._last_call = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            if elapsed < self.min_interval:
                await asyncio.sleep(self.min_interval - elapsed)
            self._last_call = time.monotonic()


_rate_limiter = IDXRateLimit(min_interval=1.0)

# Cache full daily summary table (956 rows) — reused for all ticker lookups
_summary_cache: dict[str, tuple[float, dict[str, dict]]] = {}
_SUMMARY_TTL = 120.0


class IDXDataSource:
    """Async client for IDX (Indonesia Stock Exchange) public endpoints."""

    BASES = [
        "https://www.idx.co.id/primary",
        "https://idx.co.id/primary",
    ]
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8",
        "Referer": "https://www.idx.co.id/id/data-pasar/ringkasan-perdagangan/ringkasan-saham/",
        "Origin": "https://www.idx.co.id",
    }
    TIMEOUT = aiohttp.ClientTimeout(total=12)

    def __init__(self):
        pass

    async def _get_session(self) -> aiohttp.ClientSession:
        from core.http_session import get_shared_session
        return await get_shared_session()

    async def close(self) -> None:
        pass

    def _fetch_json_sync(self, path: str, params: dict) -> Optional[Any]:
        """Sync fetch via curl_cffi (bypasses Cloudflare when available)."""
        if not _CURL_CFFI:
            return None
        for base in self.BASES:
            url = f"{base}/{path.lstrip('/')}"
            try:
                resp = _curl_requests.get(
                    url, params=params, headers=self.HEADERS,
                    impersonate="chrome120", timeout=15,
                )
                if resp.status_code != 200:
                    continue
                text = resp.text.strip()
                if not text or text in ("null", "[]", "{}"):
                    continue
                if text.startswith("<"):
                    continue
                return resp.json()
            except Exception as exc:
                log.debug(f"IDX curl_cffi {path}: {exc}")
        return None

    async def _fetch_json(self, path: str, params: dict,
                          timeout: Optional[aiohttp.ClientTimeout] = None) -> Optional[Any]:
        """Fetch JSON from IDX — curl_cffi preferred (Cloudflare), aiohttp fallback."""
        timeout = timeout or self.TIMEOUT

        if _CURL_CFFI:
            result = await asyncio.to_thread(self._fetch_json_sync, path, params)
            if result is not None:
                return result

        for base in self.BASES:
            url = f"{base}/{path.lstrip('/')}"
            try:
                sess = await self._get_session()
                async with sess.get(url, params=params, timeout=timeout) as resp:
                    if resp.status != 200:
                        continue
                    text = await resp.text()
                    if not text or text.strip() in ("null", "[]", "{}"):
                        continue
                    if text.strip().startswith("<"):
                        continue
                    return await resp.json(content_type=None)
            except Exception as exc:
                log.debug(f"IDX aiohttp {path}: {exc}")
        return None

    async def _get_summary_table(self, trade_date: str) -> dict[str, dict]:
        """Fetch & cache full daily stock summary for a trade date."""
        cached = _summary_cache.get(trade_date)
        if cached and (time.monotonic() - cached[0]) < _SUMMARY_TTL:
            return cached[1]

        await _rate_limiter.wait()
        data = await self._fetch_json(
            "TradingSummary/GetStockSummary",
            {"date": trade_date, "start": 0, "length": 1000},
            timeout=aiohttp.ClientTimeout(total=20),
        )
        rows = (data or {}).get("data") or []
        table = {
            (row.get("StockCode") or "").strip().upper(): row
            for row in rows
            if row.get("StockCode")
        }
        if table:
            _summary_cache[trade_date] = (time.monotonic(), table)
        return table

    async def _resolve_trade_date(self, max_lookback: int = 7) -> tuple[str, dict[str, dict]]:
        """Find most recent trading date with available summary data."""
        for days_back in range(max_lookback):
            dt = datetime.now() - timedelta(days=days_back)
            trade_date = dt.strftime("%Y%m%d")
            table = await self._get_summary_table(trade_date)
            if table:
                return trade_date, table
        return "", {}

    @staticmethod
    def _row_to_summary(ticker: str, row: dict, trade_date: str) -> DailySummary:
        prev = float(row.get("Previous") or 0)
        close = float(row.get("Close") or 0)
        open_ = float(row.get("OpenPrice") or row.get("Open") or 0)
        high = float(row.get("High") or 0)
        low = float(row.get("Low") or 0)
        vol = int(float(row.get("Volume") or 0))
        val = float(row.get("Value") or 0)
        freq = int(float(row.get("Frequency") or 0))
        fb = int(float(row.get("ForeignBuy") or 0))
        fs = int(float(row.get("ForeignSell") or 0))
        change = ((close - prev) / prev * 100) if prev > 0 else float(row.get("persen") or 0)

        date_iso = trade_date
        if len(trade_date) == 8:
            date_iso = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"

        return DailySummary(
            ticker=ticker.upper(),
            date=date_iso,
            open=open_, high=high, low=low, close=close,
            volume=vol, value=val, frequency=freq,
            change_pct=round(change, 2),
            foreign_buy=fb, foreign_sell=fs, foreign_net=fb - fs,
            source="IDX Official",
        )

    async def get_daily_summary(self, ticker: str) -> Optional[DailySummary]:
        """Fetch latest daily summary from IDX primary API."""
        ticker = ticker.upper().strip()
        try:
            trade_date, table = await self._resolve_trade_date()
            row = table.get(ticker)
            if not row:
                return None
            return self._row_to_summary(ticker, row, trade_date)
        except Exception as exc:
            log.debug(f"IDX daily summary {ticker}: {exc}")
            return None

    async def get_foreign_flow(self, ticker: str) -> Optional[dict]:
        """Fetch foreign net buy/sell from IDX daily summary."""
        summary = await self.get_daily_summary(ticker)
        if not summary:
            return None

        fn = summary.foreign_net
        signal = "🟢 Net Buy" if fn > 0 else ("🔴 Net Sell" if fn < 0 else "⚪ Neutral")
        return {
            "foreign_buy": summary.foreign_buy,
            "foreign_sell": summary.foreign_sell,
            "net_raw": fn,
            "signal": signal,
            "source": "IDX Official",
        }

    async def get_historical(self, ticker: str, days: int = 365) -> list[HistoricalBar]:
        """Fetch historical EOD bars — best-effort via daily summary backfill.

        The legacy HistoricalData endpoint is often unavailable (503).
        We fetch up to ~30 recent trading days via GetStockSummary per date.
        For full history, stock_service falls back to yfinance.
        """
        ticker = ticker.upper().strip()
        max_days = min(days, 30)
        bars: list[HistoricalBar] = []

        for days_back in range(1, max_days + 1):
            dt = datetime.now() - timedelta(days=days_back)
            if dt.weekday() >= 5:
                continue
            trade_date = dt.strftime("%Y%m%d")
            table = await self._get_summary_table(trade_date)
            row = table.get(ticker)
            if not row:
                continue
            try:
                date_iso = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
                bars.append(HistoricalBar(
                    date=date_iso,
                    open=float(row.get("OpenPrice") or row.get("Open") or 0),
                    high=float(row.get("High") or 0),
                    low=float(row.get("Low") or 0),
                    close=float(row.get("Close") or 0),
                    volume=int(float(row.get("Volume") or 0)),
                ))
            except (TypeError, ValueError):
                continue

        return sorted(bars, key=lambda b: b.date)

    async def get_historical_dataframe(self, ticker: str,
                                      days: int = 365) -> pd.DataFrame:
        """Convenience: return historical as pandas DataFrame (yfinance-compatible)."""
        bars = await self.get_historical(ticker, days)
        if not bars:
            return pd.DataFrame()
        df = pd.DataFrame([{
            "Date": pd.to_datetime(b.date),
            "Open": b.open,
            "High": b.high,
            "Low": b.low,
            "Close": b.close,
            "Volume": b.volume,
        } for b in bars])
        return df.set_index("Date")


# Singleton
_source: Optional[IDXDataSource] = None


def get_idx_source() -> IDXDataSource:
    """Get singleton IDX data source."""
    global _source
    if _source is None:
        _source = IDXDataSource()
    return _source


async def close_idx_source() -> None:
    """Cleanup on shutdown."""
    global _source
    if _source is not None:
        await _source.close()
        _source = None
