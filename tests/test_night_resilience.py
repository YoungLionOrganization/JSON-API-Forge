from __future__ import annotations

import asyncio
import json

import pytest

from framework.cache import CacheManager, MemoryTTLCache
from framework.events import RedisEventHub


@pytest.mark.asyncio
@pytest.mark.parametrize("expiry", [None, "broken", [], {}, True, float("nan"), float("inf"), float("-inf"), 10**400])
async def test_corrupt_cache_expiry_reloads_instead_of_crashing(expiry):
    backend = MemoryTTLCache()
    manager = CacheManager(backend)
    await backend.set("key", json.dumps({"__forge_cache_v": 1, "fresh_until": expiry, "value": "stale"}).encode(), 10)

    async def load():
        return {"healthy": True}

    try:
        assert await manager.get_or_set_json("key", 10, load) == ({"healthy": True}, False)
        assert await manager.get_or_set_json("key", 10, load) == ({"healthy": True}, True)
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_incomplete_cache_envelope_is_not_returned_to_the_caller():
    backend = MemoryTTLCache()
    manager = CacheManager(backend)
    try:
        await backend.set("key", b'{"__forge_cache_v":1,"fresh_until":123}', 10)
        assert await manager.get_json_state("key") == (None, "miss")
        assert await backend.get("key") is None
    finally:
        await manager.close()


class PubSub:
    def __init__(self, *, failure=None, terminate=False):
        self.failure = failure
        self.terminate = terminate
        self.subscribed = asyncio.Event()
        self.closed = False
        self.unsubscribed = False

    async def subscribe(self, _channel):
        if self.failure is not None:
            raise self.failure
        self.subscribed.set()

    async def listen(self):
        if not self.terminate:
            await asyncio.Event().wait()
        if False:
            yield {}

    async def unsubscribe(self, _channel):
        self.unsubscribed = True

    async def aclose(self):
        self.closed = True


class Redis:
    def __init__(self, pubsub):
        self.subscription = pubsub
        self.published = []

    def pubsub(self):
        return self.subscription

    async def publish(self, *args):
        self.published.append(args)
        return 1

    async def aclose(self):
        pass


def hub_for(pubsub):
    hub = RedisEventHub("redis://localhost", "test", "p")
    hub.redis = Redis(pubsub)
    return hub


@pytest.mark.asyncio
async def test_redis_subscribe_failure_closes_resources_and_allows_retry():
    pubsub = PubSub(failure=ConnectionError("disconnected"))
    hub = hub_for(pubsub)
    try:
        with pytest.raises(ConnectionError, match="disconnected"):
            await hub.publish("messages", {"message": "hello"})
        assert pubsub.closed and pubsub.unsubscribed
        assert hub.redis.published == []
        healthy = PubSub()
        hub.redis.subscription = healthy
        assert await hub.publish("messages", {"message": "retry"}) == 1
        assert not healthy.closed
    finally:
        await hub.close()
    assert healthy.closed


@pytest.mark.asyncio
async def test_redis_finished_listener_never_reports_successful_authorization():
    pubsub = PubSub(terminate=True)
    hub = hub_for(pubsub)
    try:
        with pytest.raises(RuntimeError, match="listener stopped"):
            await hub.publish("messages", {"message": "hello"})
        assert pubsub.closed and hub.redis.published == []
    finally:
        await hub.close()


@pytest.mark.asyncio
async def test_cancelling_subscription_establishment_closes_resources():
    pubsub = PubSub()
    started = asyncio.Event()

    async def stalled_subscribe(_channel):
        started.set()
        await asyncio.Event().wait()

    pubsub.subscribe = stalled_subscribe
    hub = hub_for(pubsub)
    task = asyncio.create_task(hub.publish("messages", {}))
    try:
        await started.wait()
        await hub.close()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert pubsub.closed and pubsub.unsubscribed and hub.redis.published == []
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
