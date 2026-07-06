from __future__ import annotations
import asyncio
from typing import Optional
from utils.logger import get_logger
from utils.market_regime import get_coal_price, get_ihsg, get_ihsg_regime, get_top_movers
from utils.news_utils import detect_special_news, get_berita as _news
from utils.scan_utils import scan_all_liquid_idx as _scan
from utils.sector_data import get_sector_label
from utils.stock_service import get_stock_data as _gsd
from utils.ticker_utils import normalize_ticker

log = get_logger("data.fetcher")


def get_sector_for_ticker(t: str) -> str:
    return get_sector_label(t)

async def fetch_ticker_data(ticker: str) -> Optional[dict]:
    try:
        ticker = normalize_ticker(ticker)
    except ValueError as exc:
        return {"error": str(exc)}
    try:
        from core.cache import get_cache
        cache = await get_cache()
        cached = await cache.get_ticker(ticker)
        if cached: return cached
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, _gsd, ticker)
        if data and "error" not in data:
            await cache.set_ticker(ticker, data, ttl=300)
        return data
    except Exception as exc:
        log.warning(f"fetch_ticker_data {ticker}: {exc}")
        try: return _gsd(ticker)
        except Exception: return None

async def fetch_news(ticker: str, n: int = 5) -> list[dict]:
    loop = asyncio.get_event_loop()
    try: return await loop.run_in_executor(None, _news, ticker, n)
    except Exception: return []

async def scan_liquid_async(top_n: int = 30, use_cache: bool = True) -> list[dict]:
    if use_cache:
        try:
            from core.cache import get_cache
            c = await get_cache()
            cached = await c.get_scan()
            if cached: return cached
        except Exception: pass
    loop = asyncio.get_event_loop()
    try:
        results = await loop.run_in_executor(None, lambda: _scan(top_n=top_n))
        if results:
            try:
                from core.cache import get_cache
                c = await get_cache()
                await c.set_scan(results, ttl=1800)
            except Exception: pass
        return results or []
    except Exception as exc:
        log.error(f"scan_liquid_async: {exc}"); return []
