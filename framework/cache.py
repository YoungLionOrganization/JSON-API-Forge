from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict
from hashlib import sha256
from typing import Any

log = logging.getLogger("json_api_forge.cache")


class MemoryTTLCache:
    def __init__(self, max_entries: int = 10_000):
        self.max_entries = max_entries
        self._data: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
        self._lock = asyncio.Lock()
        self._generations: dict[str, int] = {}

    async def get(self, key: str) -> bytes | None:
        now = time.monotonic()
        async with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            expires_at, value = item
            if expires_at <= now:
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return value

    async def set(self, key: str, value: bytes, ttl: int) -> None:
        async with self._lock:
            self._data[key] = (time.monotonic() + max(ttl, 1), value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._data.pop(key, None)

    async def generation(self, namespace: str) -> int:
        return self._generations.get(namespace, 0)

    async def bump_generation(self, namespace: str) -> int:
        async with self._lock:
            value = self._generations.get(namespace, 0) + 1
            self._generations[namespace] = value
            return value

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        async with self._lock:
            self._data.clear()


class RedisTTLCache:
    def __init__(self, url: str, prefix: str = "forge"):
        from redis.asyncio import from_url

        self.redis = from_url(url, encoding=None, decode_responses=False)
        self.prefix = prefix

    def _key(self, key: str) -> str:
        return f"{self.prefix}:cache:{key}"

    def _gen_key(self, namespace: str) -> str:
        return f"{self.prefix}:gen:{namespace}"

    async def get(self, key: str) -> bytes | None:
        return await self.redis.get(self._key(key))

    async def set(self, key: str, value: bytes, ttl: int) -> None:
        await self.redis.set(self._key(key), value, ex=max(ttl, 1))

    async def delete(self, key: str) -> None:
        await self.redis.delete(self._key(key))

    async def generation(self, namespace: str) -> int:
        value = await self.redis.get(self._gen_key(namespace))
        return int(value or 0)

    async def bump_generation(self, namespace: str) -> int:
        return int(await self.redis.incr(self._gen_key(namespace)))

    def distributed_lock(self, key: str, timeout: int = 15, blocking_timeout: int = 3):
        digest = sha256(key.encode("utf-8")).hexdigest()
        return self.redis.lock(f"{self.prefix}:lock:{digest}", timeout=timeout, blocking_timeout=blocking_timeout)

    async def ping(self) -> bool:
        return bool(await self.redis.ping())

    async def close(self) -> None:
        await self.redis.aclose()


class TieredCache:
    """L1 process cache + L2 Redis. Redis is authoritative for generation counters."""

    def __init__(self, memory: MemoryTTLCache, redis: RedisTTLCache):
        self.memory, self.redis = memory, redis

    async def get(self, key: str) -> bytes | None:
        value = await self.memory.get(key)
        if value is not None:
            return value
        value = await self.redis.get(key)
        if value is not None:
            await self.memory.set(key, value, ttl=5)
        return value

    async def set(self, key: str, value: bytes, ttl: int) -> None:
        await asyncio.gather(self.redis.set(key, value, ttl), self.memory.set(key, value, min(ttl, 10)))

    async def delete(self, key: str) -> None:
        await asyncio.gather(self.memory.delete(key), self.redis.delete(key))

    async def generation(self, namespace: str) -> int:
        return await self.redis.generation(namespace)

    async def bump_generation(self, namespace: str) -> int:
        value = await self.redis.bump_generation(namespace)
        await self.memory.bump_generation(namespace)
        return value

    def distributed_lock(self, key: str, timeout: int = 15, blocking_timeout: int = 3):
        return self.redis.distributed_lock(key, timeout=timeout, blocking_timeout=blocking_timeout)

    async def ping(self) -> bool:
        return await self.redis.ping()

    async def close(self) -> None:
        await asyncio.gather(self.memory.close(), self.redis.close())


class CacheManager:
    """Read-through JSON cache with generations, stampede locks and optional stale-while-revalidate."""

    def __init__(self, backend, prefix: str = "forge", *, fail_open: bool = True):
        self.backend, self.prefix, self.fail_open = backend, prefix, fail_open
        self._locks: dict[str, tuple[asyncio.Lock, int]] = {}
        self._locks_guard = asyncio.Lock()
        self._refreshing: set[str] = set()
        self._refresh_guard = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()

    async def _lock_for(self, key: str) -> asyncio.Lock:
        """Borrow a per-key lock and track active/waiting users.

        The registry contains only keys currently participating in a load. A hostile
        stream of unique cache keys therefore cannot leave an unbounded lock map.
        """
        async with self._locks_guard:
            entry = self._locks.get(key)
            if entry is None:
                lock = asyncio.Lock()
                self._locks[key] = (lock, 1)
                return lock
            lock, users = entry
            self._locks[key] = (lock, users + 1)
            return lock

    async def _release_lock(self, key: str, lock: asyncio.Lock) -> None:
        async with self._locks_guard:
            entry = self._locks.get(key)
            if entry is None or entry[0] is not lock:
                return
            users = entry[1] - 1
            if users <= 0:
                self._locks.pop(key, None)
            else:
                self._locks[key] = (lock, users)

    async def generation(self, namespace: str) -> int | None:
        try:
            return await self.backend.generation(namespace)
        except Exception:
            if self.fail_open:
                log.warning("Cache generation lookup failed open namespace=%s", namespace, exc_info=True)
                return None
            raise

    async def make_key(self, namespace: str, payload: Any) -> str:
        generation = await self.generation(namespace)
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        digest = sha256(raw).hexdigest()
        if generation is None:
            return f"{namespace}:bypass:{time.monotonic_ns()}:{digest}"
        return f"{namespace}:g{generation}:{digest}"

    async def _raw_get(self, key: str) -> bytes | None:
        try:
            return await self.backend.get(key)
        except Exception:
            if self.fail_open:
                log.warning("Cache read failed open key=%s", key, exc_info=True)
                return None
            raise

    async def get_json_state(self, key: str) -> tuple[Any | None, str]:
        raw = await self._raw_get(key)
        if raw is None:
            return None, "miss"
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
            log.warning("Discarding malformed cache entry key=%s", key)
            try:
                await self.backend.delete(key)
            except Exception:
                log.warning("Failed to delete malformed cache entry key=%s", key, exc_info=True)
            return None, "miss"
        if isinstance(parsed, dict) and parsed.get("__forge_cache_v") == 1 and "value" in parsed:
            state = "fresh" if time.time() <= float(parsed.get("fresh_until", 0)) else "stale"
            return parsed["value"], state
        return parsed, "fresh"

    async def get_json(self, key: str) -> Any | None:
        value, state = await self.get_json_state(key)
        return value if state != "miss" else None

    async def set_json(self, key: str, value: Any, ttl: int, stale_ttl: int = 0) -> None:
        envelope = {"__forge_cache_v": 1, "fresh_until": time.time() + max(ttl, 1), "value": value}
        payload = json.dumps(envelope, separators=(",", ":"), default=str).encode("utf-8")
        try:
            await self.backend.set(key, payload, max(ttl + max(stale_ttl, 0), 1))
        except Exception:
            if not self.fail_open:
                raise
            log.warning("Cache write failed open key=%s", key, exc_info=True)

    async def _load_locked(self, key: str, ttl: int, stale_ttl: int, loader):
        lock = await self._lock_for(key)
        try:
            async with lock:
                value, state = await self.get_json_state(key)
                if state == "fresh":
                    return value, True
                distributed_factory = getattr(self.backend, "distributed_lock", None)
                distributed_lock = distributed_factory(key) if distributed_factory is not None else None
                acquired = False
                if distributed_lock is not None:
                    try:
                        acquired = bool(await distributed_lock.acquire())
                    except Exception:
                        log.warning("Distributed cache lock acquisition failed key=%s", key, exc_info=True)
                        acquired = False
                try:
                    if acquired:
                        value, state = await self.get_json_state(key)
                        if state == "fresh":
                            return value, True
                    value = await loader()
                    await self.set_json(key, value, ttl, stale_ttl)
                    return value, False
                finally:
                    if acquired:
                        try:
                            await distributed_lock.release()
                        except Exception:
                            log.warning("Distributed cache lock release failed key=%s", key, exc_info=True)
        finally:
            await self._release_lock(key, lock)

    async def _refresh(self, key: str, ttl: int, stale_ttl: int, loader) -> None:
        try:
            await self._load_locked(key, ttl, stale_ttl, loader)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Background cache refresh failed key=%s error=%s", key, type(exc).__name__, exc_info=True)
        finally:
            async with self._refresh_guard:
                self._refreshing.discard(key)

    async def get_or_set_json(self, key: str, ttl: int, loader, stale_ttl: int = 0):
        cached, state = await self.get_json_state(key)
        if state == "fresh":
            return cached, True
        if state == "stale" and stale_ttl > 0:
            async with self._refresh_guard:
                if key not in self._refreshing:
                    self._refreshing.add(key)
                    task = asyncio.create_task(self._refresh(key, ttl, stale_ttl, loader), name="forge-cache-refresh")
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
            return cached, True
        return await self._load_locked(key, ttl, stale_ttl, loader)

    async def invalidate_namespace(self, namespace: str) -> int:
        try:
            return await self.backend.bump_generation(namespace)
        except Exception:
            if self.fail_open:
                log.warning("Cache invalidation failed open namespace=%s", namespace, exc_info=True)
                return -1
            raise

    async def ping(self) -> bool:
        ping = getattr(self.backend, "ping", None)
        return True if ping is None else bool(await ping())

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.backend.close()


def build_cache(config, redis_url: str | None):
    if not config.enabled:
        return None
    if config.backend == "memory":
        return CacheManager(MemoryTTLCache(config.max_entries), config.key_prefix, fail_open=config.fail_open)
    if not redis_url:
        raise RuntimeError(f"cache.backend={config.backend!r} requires REDIS_URL")
    redis_cache = RedisTTLCache(redis_url, config.key_prefix)
    if config.backend == "redis":
        return CacheManager(redis_cache, config.key_prefix, fail_open=config.fail_open)
    return CacheManager(TieredCache(MemoryTTLCache(config.max_entries), redis_cache), config.key_prefix, fail_open=config.fail_open)
