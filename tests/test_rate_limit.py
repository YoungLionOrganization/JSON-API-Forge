import asyncio
import sys
import types

import pytest
from fastapi import HTTPException

from framework.rate_limit import MemoryRateLimiter, RedisRateLimiter


def test_memory_token_bucket_rejects_over_burst():
    async def run():
        limiter = MemoryRateLimiter()
        await limiter.check("x", 1, 60, 1)
        try:
            await limiter.check("x", 1, 60, 1)
        except HTTPException as exc:
            assert exc.status_code == 429
        else:
            raise AssertionError("expected rate limit")

    asyncio.run(run())


def test_memory_cleanup_and_redis_limiter_lifecycle(monkeypatch):
    class Redis:
        def __init__(self):
            self.allowed = 1
            self.closed = False

        async def eval(self, *_args):
            return [self.allowed, "0.25"]

        async def ping(self):
            return True

        async def aclose(self):
            self.closed = True

    redis = Redis()
    redis_asyncio = types.ModuleType("redis.asyncio")
    redis_asyncio.from_url = lambda *_args, **_kwargs: redis
    redis_package = types.ModuleType("redis")
    redis_package.asyncio = redis_asyncio
    monkeypatch.setitem(sys.modules, "redis", redis_package)
    monkeypatch.setitem(sys.modules, "redis.asyncio", redis_asyncio)

    async def run():
        memory = MemoryRateLimiter(max_buckets=100)
        await memory.check("expired", 10, 1)
        memory._buckets["expired"].safe_expire_at = 0
        memory._next_cleanup = 0
        await memory.check("current", 10, 1)
        assert "expired" not in memory._buckets
        assert await memory.ping()
        await memory.close()

        limiter = RedisRateLimiter("redis://test", prefix="forge:")
        await limiter.check("identity", 10, 60, 2)
        assert await limiter.ping()
        redis.allowed = 0
        with pytest.raises(HTTPException) as denied:
            await limiter.check("identity", 10, 60, 2)
        assert denied.value.status_code == 429
        await limiter.close()
        assert redis.closed

    asyncio.run(run())
