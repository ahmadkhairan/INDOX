from __future__ import annotations
import asyncio, json
from typing import Any, Optional
from utils.logger import get_logger

log = get_logger("core.cache")

try:
    import redis.asyncio as aioredis
    _REDIS_OK = True
except ImportError:
    _REDIS_OK = False

class CacheManager:
    def __init__(self, url: str) -> None:
        self._url = url; self._client: Any = None
        self._mem: dict[str, tuple[Any, float]] = {}
        self._use_redis = _REDIS_OK

    async def connect(self) -> None:
        if not self._use_redis:
            log.info("Cache: in-memory mode"); return
        try:
            self._client = aioredis.from_url(
                self._url, encoding="utf-8", decode_responses=True,
                socket_connect_timeout=3, socket_timeout=3,
            )
            await self._client.ping()
            log.info(f"Cache: Redis OK")
        except Exception as exc:
            log.warning(f"Cache: Redis fallback in-memory ({exc})")
            self._client = None; self._use_redis = False

    async def get(self, key: str) -> Optional[Any]:
        if self._client:
            try:
                raw = await self._client.get(key)
                return json.loads(raw) if raw is not None else None
            except Exception: return None
        entry = self._mem.get(key)
        if entry is None: return None
        val, exp = entry
        if exp > 0 and asyncio.get_event_loop().time() > exp:
            del self._mem[key]; return None
        return val

    async def set(self, key: str, value: Any, ttl: int = 300) -> None:
        if self._client:
            try:
                await self._client.set(key, json.dumps(value, default=str), ex=ttl); return
            except Exception: pass
        exp = asyncio.get_event_loop().time() + ttl if ttl > 0 else -1
        self._mem[key] = (value, exp)

    async def delete(self, key: str) -> None:
        if self._client:
            try: await self._client.delete(key)
            except Exception: pass
        self._mem.pop(key, None)

    async def get_ticker(self, t: str) -> Optional[dict]: return await self.get(f"ticker:{t}")
    async def set_ticker(self, t: str, d: dict, ttl: int = 300) -> None: await self.set(f"ticker:{t}", d, ttl=ttl)
    async def get_scan(self) -> Optional[list]: return await self.get("scan:idx")
    async def set_scan(self, d: list, ttl: int = 1800) -> None: await self.set("scan:idx", d, ttl=ttl)
    async def get_sentiment(self, t: str) -> Optional[dict]: return await self.get(f"sent:{t}")
    async def set_sentiment(self, t: str, d: dict, ttl: int = 900) -> None: await self.set(f"sent:{t}", d, ttl=ttl)
    async def close(self) -> None:
        if self._client: await self._client.aclose()

_cache: Optional[CacheManager] = None

async def get_cache() -> CacheManager:
    global _cache
    if _cache is None:
        from config import REDIS_URL
        _cache = CacheManager(REDIS_URL)
        await _cache.connect()
    return _cache
