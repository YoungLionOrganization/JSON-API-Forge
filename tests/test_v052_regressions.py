from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, select
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.requests import Request

from framework.config import ResourceConfig
from framework.crud import _write_payload, batch_create_rows, list_rows
from framework.readiness import check_runtime
from framework.routers.project import _cache_variant, _resource_namespace
from framework.security import Principal


def principal():
    return Principal(kind="api_key", subject="u", roles=set(), permissions=set())


def test_cache_policy_isolation_preserves_shared_invalidation():
    a = ResourceConfig(database="primary", table="items", path="admin")
    b = a.model_copy(update={"path": "public", "hidden_fields": ["secret"]})
    project = SimpleNamespace(slug="p")
    assert _resource_namespace(project, a) == _resource_namespace(project, b)
    assert _cache_variant(a, principal()) != _cache_variant(b, principal())
    owned = a.model_copy(update={"owner_field": "owner", "owner_bypass_permission": "owners.all"})
    admin = principal()
    admin.permissions.add("owners.all")
    assert _cache_variant(owned, admin) != _cache_variant(owned, principal())


def test_batch_atomicity_datetime_and_nullable_cursor(tmp_path):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
        table = Table(
            "items",
            MetaData(),
            Column("id", Integer, primary_key=True),
            Column("name", String, unique=True),
            Column("extra", String),
            Column("at", DateTime),
            Column("amount", Integer, nullable=False, default=7),
        )
        resource = ResourceConfig(
            database="primary", table="items", path="items", batch_enabled=True, pagination_mode="cursor", cursor_field="at"
        )
        try:
            async with engine.begin() as conn:
                await conn.run_sync(table.metadata.create_all)
            await batch_create_rows(
                engine,
                table,
                resource,
                principal(),
                [{"name": "a"}, {"name": "b", "extra": "kept"}, {"name": "c", "at": "2026-01-01T10:00:00Z"}],
            )
            async with engine.connect() as conn:
                rows = (await conn.execute(select(table).order_by(table.c.id))).mappings().all()
            assert rows[1]["extra"] == "kept" and rows[0]["amount"] == 7
            assert rows[2]["at"].year == 2026
            with pytest.raises(HTTPException) as exc:
                await batch_create_rows(engine, table, resource, principal(), [{"name": "new"}, {"name": "a", "extra": "x"}])
            assert exc.value.status_code == 409
            query = b"limit=1"
            found = []
            for _ in range(3):
                req = Request({"type": "http", "query_string": query, "headers": []})
                page = await list_rows(req, engine, table, resource, principal())
                found.extend(row["name"] for row in page["items"])
                query = f"limit=1&cursor={page['next_cursor']}".encode()
            assert found == ["a", "b", "c"]
            assert _write_payload({"name": "a"}, resource, table, mode="replace")["amount"] == 7
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_readiness_bounds_slow_services_and_concurrency():
    async def run():
        active = peak = 0

        async def ping():
            nonlocal active, peak
            active += 1
            peak = max(active, peak)
            try:
                await asyncio.sleep(1)
                return True
            finally:
                active -= 1

        service = SimpleNamespace(ping=ping)
        runtime = SimpleNamespace(
            available=True, registry=None, mongo_registry=None, cache=service, limiter=service, event_hub=service, error_type=None
        )
        healthy, result = await check_runtime(runtime, detailed=True, semaphore=asyncio.Semaphore(1), timeout=0.02)
        assert not healthy and peak == 1 and active == 0
        assert set(result["services"].values()) == {"error:TimeoutError"}

    asyncio.run(run())


def test_unknown_kid_refresh_is_bounded(monkeypatch):
    import framework.security as security

    calls = []
    clock = [100.0]

    class Client:
        def __init__(self, **kwargs):
            pass

        async def request(self, *args, **kwargs):
            calls.append(1)
            return SimpleNamespace(json=lambda: {"keys": [{"kid": str(len(calls))}]})

        async def close(self):
            pass

    monkeypatch.setattr(security, "ResilientHTTPClient", Client)
    monkeypatch.setattr(security.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(security, "_jwks_cache", {})
    monkeypatch.setattr(security, "_jwks_refreshed", {})
    monkeypatch.setattr(security, "_jwks_locks", {})

    async def run():
        url = "https://keys.example.test/jwks"
        await security._get_jwks(url, ttl=60, timeout=1)
        for _ in range(20):
            await security._get_jwks(url, ttl=60, timeout=1, force_refresh=True)
        assert len(calls) == 1
        clock[0] += 6
        assert (await security._get_jwks(url, ttl=60, timeout=1, force_refresh=True))["keys"][0]["kid"] == "2"
        assert len(calls) == 2

    asyncio.run(run())


def test_jwks_outage_is_coalesced_and_recovers_after_cooldown(monkeypatch):
    import httpx

    import framework.security as security

    clock = [100.0]
    calls = []
    failing = [True]

    class Client:
        def __init__(self, **kwargs):
            pass

        async def request(self, *args, **kwargs):
            calls.append(1)
            await asyncio.sleep(0)
            if failing[0]:
                raise httpx.ConnectError("offline")
            return SimpleNamespace(json=lambda: {"keys": [{"kid": "rotated"}]})

        async def close(self):
            pass

    monkeypatch.setattr(security, "ResilientHTTPClient", Client)
    monkeypatch.setattr(security.time, "monotonic", lambda: clock[0])
    for name in ("_jwks_cache", "_jwks_refreshed", "_jwks_locks"):
        monkeypatch.setattr(security, name, {})

    async def run():
        async def fetch():
            return await security._get_jwks("https://outage.test/jwks", ttl=60, timeout=1)

        errors = await asyncio.gather(*(fetch() for _ in range(20)), return_exceptions=True)
        assert all(isinstance(error, HTTPException) and error.status_code == 503 for error in errors)
        assert len(calls) == 1
        failing[0] = False
        clock[0] += 6
        assert (await fetch())["keys"][0]["kid"] == "rotated"
        assert len(calls) == 2
        clock[0] += 61
        failing[0] = True
        with pytest.raises(HTTPException):
            await fetch()
        with pytest.raises(HTTPException):
            await fetch()
        assert len(calls) == 3

    asyncio.run(run())


@pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "-Infinity"])
def test_numeric_parameters_reject_nonfinite(raw):
    from framework.config import RequestParameterSpec
    from framework.validation import _coerce_parameter

    with pytest.raises(HTTPException) as exc:
        _coerce_parameter(raw, RequestParameterSpec(name="amount", type="number"))
    assert exc.value.status_code == 422


def test_media_cleanup_on_dedup_failure_and_cancellation(tmp_path):
    import io

    from fastapi import UploadFile

    from framework.config import MediaConfig
    from framework.media import LocalMediaStore, save_media, verify_signed_media_token

    class Engine:
        def __init__(self, error):
            self.error = error

        def connect(self):
            raise self.error

    async def run():
        store = LocalMediaStore(str(tmp_path / "media"))
        for error in (RuntimeError("unavailable"), asyncio.CancelledError()):
            with pytest.raises(type(error)):
                await save_media(
                    engine=Engine(error),
                    store=store,
                    project_slug="p",
                    config=MediaConfig(deduplicate=True, allowed_mime_types=[], allowed_extensions=[]),
                    upload=UploadFile(file=io.BytesIO(b"data"), filename="a.txt"),
                    owner_subject="u",
                )
            assert not [p for p in store.root.rglob("*") if p.is_file()]

    asyncio.run(run())
    assert not verify_signed_media_token("p", "m", "123.é", secret="secret")


def test_http_gzip_and_dns_deadline(monkeypatch):
    import gzip
    import socket
    import time

    import httpcore
    import httpx

    from framework.services.http_client import ResilientHTTPClient, _AddressPolicyBackend

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield gzip.compress(b'{"ok":true}')

    async def handle(request):
        return httpx.Response(200, headers={"Content-Encoding": "gzip", "Content-Type": "application/json"}, stream=Stream())

    def resolve(*args, **kwargs):
        time.sleep(0.05)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)

    async def run():
        client = ResilientHTTPClient()
        await client.client.aclose()
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        try:
            response = await client.request("GET", "https://example.test", max_response_bytes=100)
            assert response.json() == {"ok": True}
            assert "content-encoding" not in response.headers
            with pytest.raises(httpcore.ConnectTimeout):
                await _AddressPolicyBackend(block_private_networks=True).connect_tcp("example.test", 443, timeout=0.005)

            async def slow(*args, **kwargs):
                await asyncio.sleep(1)

            monkeypatch.setattr(client, "_send_once", slow)
            with pytest.raises(httpx.TimeoutException):
                await client.request("GET", "https://example.test", total_timeout=0.005)
        finally:
            await client.close()

    asyncio.run(run())


def test_bigint_dialect_mapping():
    from sqlalchemy.dialects import postgresql, sqlite

    from framework.config import ColumnConfig
    from framework.db import _column_type

    column_type = _column_type(ColumnConfig(type="bigint"))
    assert column_type.compile(dialect=postgresql.dialect()) == "BIGINT"
    assert column_type.compile(dialect=sqlite.dialect()) == "INTEGER"


def test_stage_metrics_cover_failures_and_disabled_collection(monkeypatch):
    import framework.observability as obs

    with pytest.raises(RuntimeError), obs.measure_stage("p", "authentication"):
        raise RuntimeError("test")
    payload, _ = obs.metrics_payload()
    assert b'json_api_forge_stage_duration_seconds_count{project="p",stage="authentication"}' in payload
    monkeypatch.setattr(obs, "_STAGES", None)
    with obs.measure_stage("p", "authentication"):
        pass


def test_http_cache_hides_secrets_and_invalidates_all_resource_views(tmp_path):
    import httpx
    from fastapi import FastAPI
    from sqlalchemy import insert

    from framework.cache import CacheManager, MemoryTTLCache
    from framework.config import ProjectConfig
    from framework.factory import _hidden_route, _hide_internal_signature, _invoke_hook
    from framework.routers.project import register_project_routes
    from framework.security import has_permission

    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cache.db'}")
        table = Table("items", MetaData(), Column("id", Integer, primary_key=True), Column("name", String), Column("secret", String))
        cache = CacheManager(MemoryTTLCache())
        project = ProjectConfig.model_validate(
            {
                "slug": "p",
                "name": "Cache isolation",
                "api_prefix": "/api/p",
                "databases": {"primary": {"url": str(engine.url)}},
                "cache": {"enabled": True, "cache_lists": True, "cache_reads": True},
                "resources": [
                    {"database": "primary", "table": "items", "path": "admin", "allowed_actions": ["list", "read", "update"]},
                    {
                        "database": "primary",
                        "table": "items",
                        "path": "public",
                        "hidden_fields": ["secret"],
                        "allowed_actions": ["list", "read"],
                    },
                ],
            }
        )
        runtime = SimpleNamespace(
            config=project, registry=SimpleNamespace(engines={"primary": engine}, tables={("primary", "items"): table}), cache=cache
        )

        async def authenticate(request, runtime):
            return Principal("api_key", "reader", set(), {"*"} if request.headers.get("X-Admin") else {"public.list", "public.read"})

        def require(p, permission):
            if not has_permission(p, permission):
                raise HTTPException(status_code=403)

        app = FastAPI()
        register_project_routes(
            app=app,
            runtime=runtime,
            principal_for=authenticate,
            require=require,
            hide_internal_signature=_hide_internal_signature,
            hidden_route=_hidden_route,
            invoke_hook=_invoke_hook,
        )
        try:
            async with engine.begin() as conn:
                await conn.run_sync(table.metadata.create_all)
                await conn.execute(insert(table).values(id=1, name="before", secret="private"))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                for suffix in ("", "/1"):
                    admin = await client.get("/api/p/admin" + suffix, headers={"X-Admin": "1"})
                    assert admin.status_code == 200 and "private" in admin.text
                    for _ in range(2):
                        public = await client.get("/api/p/public" + suffix)
                        assert public.status_code == 200 and "secret" not in public.text
                assert (await client.get("/api/p/admin/1")).status_code == 403
                patched = await client.patch("/api/p/admin/1", headers={"X-Admin": "1"}, json={"name": "after"})
                assert patched.status_code == 200
                for path in ("admin", "public"):
                    for suffix in ("", "/1"):
                        response = await client.get("/api/p/" + path + suffix, headers={"X-Admin": "1"})
                        assert response.status_code == 200 and "after" in response.text and "before" not in response.text
        finally:
            await cache.close()
            await engine.dispose()

    asyncio.run(run())
