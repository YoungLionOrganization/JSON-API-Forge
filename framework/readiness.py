"""Bounded readiness probes; response shape stays compatible with v0.5.x."""

from __future__ import annotations

import asyncio

from sqlalchemy import text


async def check_runtime(runtime, *, detailed: bool, semaphore: asyncio.Semaphore, timeout: float):
    databases, mongo, shared = {}, {}, {}
    probes = []

    async def sql_ping(engine):
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True

    async def probe(target, name, call):
        async def run():
            async with semaphore:
                return await call()

        try:
            result = await asyncio.wait_for(run(), timeout)
            target[name] = "ok" if bool(result) else "error"
        except Exception as exc:
            target[name] = f"error:{type(exc).__name__}" if detailed else "error"

    if runtime.registry:
        for alias, engine in runtime.registry.engines.items():
            probes.append(probe(databases, alias, lambda engine=engine: sql_ping(engine)))
    if runtime.mongo_registry:
        for alias, client in runtime.mongo_registry.clients.items():
            probes.append(probe(mongo, alias, lambda client=client: client.admin.command("ping")))
    for name, service in (("cache", runtime.cache), ("rate_limit", runtime.limiter), ("realtime", runtime.event_hub)):
        if service is not None:
            probes.append(probe(shared, name, service.ping))
    await asyncio.gather(*probes)
    healthy = runtime.available and all(value == "ok" for target in (databases, mongo, shared) for value in target.values())
    result = {"status": "ok" if healthy else "degraded", "databases": databases, "mongo": mongo, "services": shared}
    if runtime.error_type:
        result["error_type"] = runtime.error_type
    return healthy, result
