import asyncio

from framework.cache import CacheManager, MemoryTTLCache


def test_generation_invalidation_changes_key():
    async def run():
        manager = CacheManager(MemoryTTLCache(100))
        k1 = await manager.make_key("app1:notes", {"id": 1})
        await manager.set_json(k1, {"value": "old"}, 30)
        assert await manager.get_json(k1) == {"value": "old"}
        await manager.invalidate_namespace("app1:notes")
        k2 = await manager.make_key("app1:notes", {"id": 1})
        assert k1 != k2
        assert await manager.get_json(k2) is None
        await manager.close()

    asyncio.run(run())
