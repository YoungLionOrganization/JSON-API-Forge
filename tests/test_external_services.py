"""Live dependency smoke tests used by the GitHub integration job.

These tests intentionally skip in a lightweight local checkout. CI supplies real
PostgreSQL, Redis and MongoDB service containers and installs all optional drivers.
"""

from __future__ import annotations

import os

import pytest


@pytest.mark.asyncio
async def test_postgres_service_container():
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL not configured")
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(url)
    try:
        assert await conn.fetchval("SELECT 1") == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_redis_service_container():
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL not configured")
    redis_asyncio = pytest.importorskip("redis.asyncio")
    client = redis_asyncio.from_url(url, decode_responses=True)
    try:
        assert await client.ping() is True
        await client.set("forge-ci", "ok", ex=10)
        assert await client.get("forge-ci") == "ok"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_mongo_service_container():
    url = os.getenv("TEST_MONGO_URL")
    if not url:
        pytest.skip("TEST_MONGO_URL not configured")
    pymongo = pytest.importorskip("pymongo")
    if not hasattr(pymongo, "AsyncMongoClient"):
        pytest.fail("Installed PyMongo does not expose AsyncMongoClient")
    client = pymongo.AsyncMongoClient(url, serverSelectionTimeoutMS=5000)
    try:
        assert (await client.admin.command("ping")).get("ok") == 1.0
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_postgres_forge_idempotent_transaction():
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL not configured")
    pytest.importorskip("asyncpg")
    from uuid import uuid4

    from fastapi import HTTPException
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from starlette.requests import Request

    from framework.config import OperationConfig
    from framework.operations import execute_idempotent_operation, init_operation_idempotency
    from framework.security import Principal

    engine = create_async_engine(url.replace("postgresql://", "postgresql+asyncpg://", 1), pool_pre_ping=True)
    suffix = uuid4().hex[:12]
    table = f"forge_ci_accounts_{suffix}"
    operation_name = f"transfer-{suffix}"
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE TABLE "{table}" (id TEXT PRIMARY KEY, balance INTEGER NOT NULL)'))
            await conn.execute(text(f"INSERT INTO \"{table}\" (id,balance) VALUES ('a',100),('b',0)"))
        await init_operation_idempotency(engine)
        operation = OperationConfig.model_validate(
            {
                "name": operation_name,
                "permission": "economy.transfer",
                "transaction": True,
                "idempotency": True,
                "statements": [
                    {
                        "sql": f'UPDATE "{table}" SET balance=balance-:amount WHERE id=:sender AND balance>=:amount',
                        "mode": "execute",
                        "require_rowcount_min": 1,
                        "params": {"amount": "$body.amount", "sender": "$body.sender"},
                        "result_name": "debit",
                    },
                    {
                        "sql": f'UPDATE "{table}" SET balance=balance+:amount WHERE id=:receiver',
                        "mode": "execute",
                        "require_rowcount_min": 1,
                        "params": {"amount": "$body.amount", "receiver": "$body.receiver"},
                        "result_name": "credit",
                    },
                ],
            }
        )
        principal = Principal(kind="api_key", subject="ci-bot", roles=set(), permissions={"economy.transfer"})
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/rpc/transfer",
                "headers": [],
                "query_string": b"",
                "path_params": {},
                "server": ("test", 80),
                "client": ("127.0.0.1", 1),
                "scheme": "http",
            }
        )
        request.state.validated_parameters = {}
        body = {"sender": "a", "receiver": "b", "amount": 25}
        first, replay = await execute_idempotent_operation(
            engine, project_slug="ci", operation=operation, principal=principal, raw_key="interaction-1", body=body, request=request
        )
        assert replay is False and first["debit"]["rowcount"] == 1
        second, replay = await execute_idempotent_operation(
            engine, project_slug="ci", operation=operation, principal=principal, raw_key="interaction-1", body=body, request=request
        )
        assert replay is True and second == first
        with pytest.raises(HTTPException) as conflict:
            await execute_idempotent_operation(
                engine,
                project_slug="ci",
                operation=operation,
                principal=principal,
                raw_key="interaction-1",
                body={**body, "amount": 30},
                request=request,
            )
        assert conflict.value.status_code == 409
        async with engine.connect() as conn:
            balances = dict((await conn.execute(text(f'SELECT id,balance FROM "{table}" ORDER BY id'))).all())
        assert balances == {"a": 75, "b": 25}
        with pytest.raises(HTTPException):
            await execute_idempotent_operation(
                engine,
                project_slug="ci",
                operation=operation,
                principal=principal,
                raw_key="interaction-insufficient",
                body={**body, "amount": 999},
                request=request,
            )
        async with engine.connect() as conn:
            balances_after = dict((await conn.execute(text(f'SELECT id,balance FROM "{table}" ORDER BY id'))).all())
        assert balances_after == balances
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP TABLE IF EXISTS "{table}"'))
        await engine.dispose()


@pytest.mark.asyncio
async def test_redis_forge_rate_limiter_is_distributed():
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL not configured")
    pytest.importorskip("redis.asyncio")
    from uuid import uuid4

    from fastapi import HTTPException

    from framework.rate_limit import RedisRateLimiter

    prefix = f"forge-ci-rate:{uuid4().hex}"
    first = RedisRateLimiter(url, prefix=prefix)
    second = RedisRateLimiter(url, prefix=prefix)
    try:
        await first.check("same-principal", 2, 60, 2)
        await second.check("same-principal", 2, 60, 2)
        with pytest.raises(HTTPException) as exc:
            await first.check("same-principal", 2, 60, 2)
        assert exc.value.status_code == 429
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_mongo_forge_crud_with_tenant_isolation():
    url = os.getenv("TEST_MONGO_URL")
    if not url:
        pytest.skip("TEST_MONGO_URL not configured")
    pymongo = pytest.importorskip("pymongo")
    from uuid import uuid4

    from starlette.requests import Request

    from framework.config import MongoResourceConfig
    from framework.mongo import create_document, delete_document, get_document, list_documents
    from framework.security import Principal

    client = pymongo.AsyncMongoClient(url, serverSelectionTimeoutMS=5000)
    db = client["forge_test"]
    collection = f"profiles_{uuid4().hex[:12]}"
    resource = MongoResourceConfig.model_validate(
        {
            "database": "main",
            "collection": collection,
            "path": "profiles",
            "tenant_field": "tenant_id",
            "allowed_filters": ["name"],
            "allowed_sort": ["name"],
            "writable_fields": ["name"],
        }
    )
    t1 = Principal(kind="api_key", subject="one", roles=set(), permissions=set(), tenant_id="t1")
    t2 = Principal(kind="api_key", subject="two", roles=set(), permissions=set(), tenant_id="t2")
    try:
        one = await create_document(db, resource, t1, {"name": "Alice"})
        await create_document(db, resource, t2, {"name": "Bob"})
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/profiles",
                "headers": [],
                "query_string": b"",
                "server": ("test", 80),
                "client": ("127.0.0.1", 1),
                "scheme": "http",
            }
        )
        listed = await list_documents(request, db, resource, t1)
        assert [row["name"] for row in listed["items"]] == ["Alice"]
        assert (await get_document(db, resource, t1, one["_id"]))["name"] == "Alice"
        await delete_document(db, resource, t1, one["_id"])
    finally:
        await db[collection].drop()
        await client.close()


@pytest.mark.asyncio
async def test_redis_event_hub_cross_instance_delivery():
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL not configured")
    pytest.importorskip("redis.asyncio")
    import asyncio
    from uuid import uuid4

    from framework.events import RedisEventHub

    suffix = uuid4().hex
    publisher = RedisEventHub(url, f"forge-ci-events:{suffix}", "project")
    subscriber = RedisEventHub(url, f"forge-ci-events:{suffix}", "project")

    async def receive_one():
        stream = subscriber.subscribe("notifications", 8)
        try:
            return await asyncio.wait_for(anext(stream), timeout=5)
        finally:
            await stream.aclose()

    task = asyncio.create_task(receive_one())
    try:
        await subscriber._ensure_listener("notifications")
        delivered = await publisher.publish("notifications", {"kind": "ready", "n": 1})
        assert delivered >= 1
        assert await task == {"kind": "ready", "n": 1}
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await publisher.close()
        await subscriber.close()
