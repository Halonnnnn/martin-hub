import asyncio
import time
from typing import Protocol

from cachetools import TTLCache


class CacheBackend(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl: int) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def set_if_absent(self, key: str, value: str, ttl: int) -> bool: ...
    async def incr(self, key: str, ttl: int) -> int: ...


class MemoryCacheBackend:
    """进程内 TTL 字典。重启后计数归零，为文档化行为（T-G04）。"""

    def __init__(self, maxsize: int = 100_000, ttl: int = 24 * 3600):
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl)
        self._expires: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def _evict_if_expired(self, key: str) -> None:
        exp = self._expires.get(key)
        if exp is not None and exp <= time.monotonic():
            self._cache.pop(key, None)
            self._expires.pop(key, None)

    async def get(self, key: str) -> str | None:
        async with self._lock:
            await self._evict_if_expired(key)
            v = self._cache.get(key)
            return None if v is None else str(v)

    async def set(self, key: str, value: str, ttl: int) -> None:
        async with self._lock:
            self._cache[key] = value
            self._expires[key] = time.monotonic() + ttl

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._cache.pop(key, None)
            self._expires.pop(key, None)

    async def set_if_absent(self, key: str, value: str, ttl: int) -> bool:
        async with self._lock:
            await self._evict_if_expired(key)
            if key in self._cache:
                return False
            self._cache[key] = value
            self._expires[key] = time.monotonic() + ttl
            return True

    async def incr(self, key: str, ttl: int) -> int:
        async with self._lock:
            await self._evict_if_expired(key)
            cur = int(self._cache.get(key, 0))
            cur += 1
            self._cache[key] = str(cur)
            self._expires[key] = time.monotonic() + ttl
            return cur


def build_cache(settings) -> CacheBackend:
    if settings.cache_backend == "redis":
        raise NotImplementedError("RedisCacheBackend 在 M1 实现")
    return MemoryCacheBackend()
