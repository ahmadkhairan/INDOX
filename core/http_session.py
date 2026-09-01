# core/http_session.py — Shared aiohttp ClientSession lifecycle manager
from __future__ import annotations

import asyncio
from typing import Optional
import aiohttp

from utils.logger import get_logger

log = get_logger("core.http")

_session: Optional[aiohttp.ClientSession] = None
_lock = asyncio.Lock()


async def get_shared_session() -> aiohttp.ClientSession:
    """Get or create the singleton shared aiohttp.ClientSession."""
    global _session
    if _session is None or _session.closed:
        async with _lock:
            if _session is None or _session.closed:
                timeout = aiohttp.ClientTimeout(total=20, connect=10)
                _session = aiohttp.ClientSession(
                    timeout=timeout,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                        ),
                        "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8",
                    },
                )
                log.debug("Created shared aiohttp.ClientSession")
    return _session


async def close_shared_session() -> None:
    """Gracefully close the singleton shared aiohttp.ClientSession."""
    global _session
    if _session is not None and not _session.closed:
        try:
            await _session.close()
            # Allow underlying SSL transports to close gracefully without ResourceWarning
            await asyncio.sleep(0.25)
            log.info("Closed shared aiohttp.ClientSession")
        except Exception as exc:
            log.warning(f"Error closing shared aiohttp session: {exc}")
        finally:
            _session = None
