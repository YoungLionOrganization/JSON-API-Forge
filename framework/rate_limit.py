from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from hashlib import sha256

from fastapi import HTTPException


@dataclass(slots=True)
class Bucket:
    tokens: float
    updated: float
    last_seen: float
    safe_expire_at: float = 0.0


class MemoryRateLimiter:
    def __init__(self, *, max_buckets: int = 50_000, idle_ttl_seconds: int = 600, cleanup_interval_seconds: int = 30):
        self.max_buckets = max(100, int(max_buckets))
        self.idle_ttl_seconds = max(10, int(idle_ttl_seconds))
        self.cleanup_interval_seconds = max(1, int(cleanup_interval_seconds))
        self._buckets: dict[str, Bucket] = {}
        self._overflow_buckets: dict[str, Bucket] = {}
        self._lock = asyncio.Lock()
        self._next_cleanup = time.monotonic() + self.cleanup_interval_seconds

    def _cleanup_locked(self, now: float) -> None:
        if now < self._next_cleanup and len(self._buckets) <= self.max_buckets:
            return
        for key in [k for k, b in self._buckets.items() if b.safe_expire_at <= now]:
            self._buckets.pop(key, None)
        self._next_cleanup = now + self.cleanup_interval_seconds

    async def check(self, identity: str, limit: int, window_seconds: int, burst: int | None = None) -> None:
        capacity = float(burst or limit)
        refill = float(limit) / max(float(window_seconds), 1.0)
        safe_idle = max(float(self.idle_ttl_seconds), capacity / refill)
        now = time.monotonic()
        async with self._lock:
            self._cleanup_locked(now)
            key = identity
            bucket = self._buckets.get(key)
            overflow = False
            if bucket is None and len(self._buckets) >= self.max_buckets:
                key = sha256(f"{limit}:{window_seconds}:{burst or 0}".encode()).hexdigest()[:16]
                overflow = True
                bucket = self._overflow_buckets.get(key)
            if bucket is None:
                bucket = Bucket(capacity, now, now, now + safe_idle)
                if overflow:
                    if len(self._overflow_buckets) >= 128 and key not in self._overflow_buckets:
                        key = "shared"
                        bucket = self._overflow_buckets.get(key) or bucket
                    self._overflow_buckets[key] = bucket
                else:
                    self._buckets[key] = bucket
            bucket.tokens = min(capacity, bucket.tokens + (now - bucket.updated) * refill)
            bucket.updated = now
            bucket.last_seen = now
            bucket.safe_expire_at = now + safe_idle
            if bucket.tokens < 1.0:
                raise HTTPException(
                    status_code=429,
                    detail="Rate limit exceeded",
                    headers={"Retry-After": str(max(1, math.ceil((1.0 - bucket.tokens) / refill)))},
                )
            bucket.tokens -= 1.0

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        async with self._lock:
            self._buckets.clear()
            self._overflow_buckets.clear()


class RedisRateLimiter:
    LUA = """
    local key = KEYS[1]
    local rate = tonumber(ARGV[1])
    local capacity = tonumber(ARGV[2])
    local ttl = tonumber(ARGV[3])
    local t = redis.call('TIME')
    local now = tonumber(t[1]) + (tonumber(t[2]) / 1000000.0)
    local data = redis.call('HMGET', key, 'tokens', 'ts')
    local tokens = tonumber(data[1]); local ts = tonumber(data[2])
    if tokens == nil then tokens = capacity end
    if ts == nil then ts = now end
    if now < ts then ts = now end
    tokens = math.min(capacity, tokens + (now - ts) * rate)
    local allowed = 0
    if tokens >= 1 then tokens = tokens - 1; allowed = 1 end
    redis.call('HSET', key, 'tokens', tokens, 'ts', now)
    redis.call('EXPIRE', key, ttl)
    return {allowed, tostring(tokens)}
    """

    def __init__(self, url: str, *, prefix: str = "json-api-forge:rl"):
        from redis.asyncio import from_url

        self.redis = from_url(url, encoding="utf-8", decode_responses=True)
        self.prefix = prefix.rstrip(":")

    async def check(self, identity: str, limit: int, window_seconds: int, burst: int | None = None) -> None:
        capacity = float(burst or limit)
        rate = float(limit) / max(float(window_seconds), 1.0)
        digest = sha256(identity.encode()).hexdigest()[:40]
        key = f"{self.prefix}:{digest}"
        ttl = max(2, math.ceil(capacity / rate) + max(1, int(window_seconds)))
        allowed, tokens = await self.redis.eval(self.LUA, 1, key, rate, capacity, ttl)
        if int(allowed) != 1:
            raise HTTPException(
                status_code=429, detail="Rate limit exceeded", headers={"Retry-After": str(max(1, math.ceil((1.0 - float(tokens)) / rate)))}
            )

    async def ping(self) -> bool:
        return bool(await self.redis.ping())

    async def close(self) -> None:
        await self.redis.aclose()
