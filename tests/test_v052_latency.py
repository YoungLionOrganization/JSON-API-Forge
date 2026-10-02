"""Latency regressions use controlled delays/events, never live remote services."""

from __future__ import annotations

import asyncio
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpcore
import httpx
import pytest
from fastapi import FastAPI
from starlette.responses import StreamingResponse

from framework.config import DataSourceConfig, ForgeConfig, ProjectConfig
from framework.factory import create_app
from framework.protection import RequestExecutionTimeoutMiddleware
from framework.settings import settings


def test_request_deadline_cancels_handler_before_slot_is_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "editor_api_enabled", False)
    monkeypatch.setattr(settings, "internal_database_url", f"sqlite+aiosqlite:///{tmp_path}/internal.db")
    project = ProjectConfig.model_validate(
        {
            "slug": "latency",
            "name": "Latency",
            "databases": {"primary": {"url": f"sqlite+aiosqlite:///{tmp_path}/app.db"}},
            "rate_limit": {"enabled": False},
            "audit_enabled": False,
            "protection": {"request_timeout_seconds": 0.02, "max_concurrent_requests": 1, "reject_when_saturated": True},
        }
    )
    app = create_app(_configuration=(ForgeConfig(projects=[project]), {}))
    active = peak = cancelled = completed = 0

    @app.get("/api/latency/v1/slow")
    async def slow():
        nonlocal active, peak, cancelled, completed
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.15)
            completed += 1
            return {"ok": True}
        except asyncio.CancelledError:
            cancelled += 1
            raise
        finally:
            active -= 1

    @app.get("/api/latency/v1/fast")
    async def fast():
        return {"ok": True}

    async def run():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
                for _ in range(2):
                    response = await client.get("/api/latency/v1/slow")
                    assert response.status_code == 504
                    assert response.json() == {"detail": "Request timed out"}
                    assert "x-request-id" in response.headers
                    assert active == 0 and completed == 0
                responses = await asyncio.gather(*(client.get("/api/latency/v1/slow") for _ in range(6)))
                assert {response.status_code for response in responses} == {503, 504}
                assert peak == 1 and active == 0 and cancelled == 3
                assert app.state.runtimes["latency"].gate.active == 0
                assert (await client.get("/api/latency/v1/fast")).json() == {"ok": True}

    asyncio.run(run())


def test_deadline_preserves_streaming_errors_and_caller_cancellation():
    cancelled = []
    app = FastAPI()
    app.add_middleware(RequestExecutionTimeoutMiddleware, timeout_for_path=lambda path: 0.02)

    @app.get("/stream")
    async def stream():
        async def chunks():
            yield b"first"
            await asyncio.sleep(0.05)
            yield b"last"

        return StreamingResponse(chunks())

    @app.get("/error")
    async def error():
        raise ValueError("endpoint error")

    @app.get("/cancel")
    async def cancel():
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(True)
        return {}

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/stream")).content == b"firstlast"
            with pytest.raises(ValueError, match="endpoint error"):
                await client.get("/error")
            task = asyncio.create_task(client.get("/cancel"))
            await asyncio.sleep(0.005)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert cancelled == [True]

    asyncio.run(run())


def test_static_sources_do_not_create_http_clients_and_each_policy_is_lazy(monkeypatch):
    import framework.datasources as sources

    calls, closed = [], []

    class Client:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        async def close(self):
            closed.append(self)

    monkeypatch.setattr(sources, "ResilientHTTPClient", Client)
    project = ProjectConfig(slug="lazy", name="Lazy", databases={"primary": {"url": "sqlite+aiosqlite:///:memory:"}})

    async def run():
        unused = sources.DataSourceManager(project)
        assert calls == []
        await unused.close()
        assert calls == [] and closed == []
        manager = sources.DataSourceManager(project)
        public = manager.http
        assert manager.http is public and len(calls) == 1
        private = manager.private_http
        assert manager.private_http is private and public is not private and len(calls) == 2
        assert calls[1] == {"block_private_networks": False}
        await manager.close()
        assert closed == [public, private]

    asyncio.run(run())


def test_datasource_total_budget_covers_streaming_and_retry_backoff(monkeypatch):
    from framework.datasources import DataSourceManager

    attempts, closed = [], []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(20):
                await asyncio.sleep(0.01)
                yield b" "

        async def aclose(self):
            closed.append(True)

    async def upstream(request):
        attempts.append(True)
        return httpx.Response(200, headers={"content-type": "application/json"}, stream=Stream())

    async def run():
        manager = DataSourceManager(
            ProjectConfig(slug="budget", name="Budget", databases={"primary": {"url": "sqlite+aiosqlite:///:memory:"}})
        )
        client = manager.http
        await client.client.aclose()
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        source = DataSourceConfig(
            name="http", type="http", url="https://budget.test", public=True, timeout_seconds=0.05, total_timeout_seconds=0.03
        )
        request = SimpleNamespace(query_params=httpx.QueryParams())
        try:
            with pytest.raises(httpx.TimeoutException, match="deadline"):
                await manager.read(source, request)
            assert attempts == [True] and closed == [True]
            attempts.clear()

            async def fail(*args, **kwargs):
                attempts.append(True)
                raise httpx.ReadTimeout("stall")

            monkeypatch.setattr(client, "_send_once", fail)
            with pytest.raises(httpx.TimeoutException, match="deadline"):
                await manager.read(source, request)
            assert len(attempts) == 1  # Deadline cancels the first 250ms backoff.
            assert DataSourceConfig(name="old", type="http", url="https://budget.test", public=True).total_timeout_seconds is None
        finally:
            await manager.close()

    asyncio.run(run())


def test_timed_out_dns_does_not_occupy_file_executor(monkeypatch):
    import framework.services.http_client as http

    entered = threading.Event()
    release = threading.Event()
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-dns")

    def resolve(*args, **kwargs):
        entered.set()
        release.wait(timeout=2)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(http, "_dns_executor", lambda: pool)

    async def run():
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
        try:
            with pytest.raises(httpcore.ConnectTimeout):
                await http._AddressPolicyBackend(block_private_networks=True).connect_tcp("dns.test", 443, timeout=0.02)
            assert entered.is_set() and not release.is_set()
            assert await asyncio.wait_for(asyncio.to_thread(lambda: "file-work"), 0.2) == "file-work"
        finally:
            release.set()
            pool.shutdown(wait=True)

    asyncio.run(run())


def test_autocreate_checks_only_current_table_and_preserves_existing_schema(tmp_path, monkeypatch):
    from sqlalchemy import event, insert, select

    import framework.db as database

    probes = []
    original = database.create_async_engine

    def create_engine(*args, **kwargs):
        engine = original(*args, **kwargs)

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def record(conn, cursor, statement, parameters, context, executemany):
            if "table_info" in statement:
                probes.append(statement)

        return engine

    monkeypatch.setattr(database, "create_async_engine", create_engine)
    project = ProjectConfig.model_validate(
        {
            "slug": "ddl",
            "name": "DDL",
            "databases": {"primary": {"url": f"sqlite+aiosqlite:///{tmp_path}/db"}},
            "resources": [
                {
                    "database": "primary",
                    "table": f"t{i}",
                    "path": f"t{i}",
                    "auto_create": True,
                    "columns": {"id": {"type": "integer", "primary_key": True}},
                }
                for i in range(10)
            ],
        }
    )

    async def run():
        first = await database.build_registry(project)
        try:
            assert len(probes) == 20  # main and temp checks for each new table.
            async with first.engines["primary"].begin() as conn:
                await conn.execute(insert(first.tables[("primary", "t0")]).values(id=7))
        finally:
            await first.dispose()
        probes.clear()
        second = await database.build_registry(project)
        try:
            assert len(probes) == 10
            async with second.engines["primary"].connect() as conn:
                assert (await conn.execute(select(second.tables[("primary", "t0")].c.id))).scalar_one() == 7
        finally:
            await second.dispose()

    asyncio.run(run())


def test_jwks_valid_burst_shares_one_fetch_and_rotation_is_not_broken(monkeypatch):
    import time

    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    import framework.security as security

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key(), as_dict=True)
    key = [{**public, "kid": "old", "alg": "RS256"}]
    calls, instances = [], []

    class Client:
        def __init__(self, **kwargs):
            instances.append(self)

        async def request(self, *args, **kwargs):
            calls.append(True)
            await asyncio.sleep(0.01)
            return SimpleNamespace(json=lambda: {"keys": [dict(item) for item in key]})

        async def close(self):
            pass

    monkeypatch.setattr(security, "ResilientHTTPClient", Client)
    for name in ("_jwks_cache", "_jwks_refreshed", "_jwks_locks"):
        monkeypatch.setattr(security, name, {})
    project = ProjectConfig(
        slug="keys",
        name="Keys",
        databases={"primary": {"url": "sqlite+aiosqlite:///:memory:"}},
        security={"jwt_provider": "jwks", "jwt_jwks_url": "https://keys.test/jwks", "jwt_algorithms": ["RS256"]},
    )

    def token(kid):
        return jwt.encode({"sub": "reader", "exp": time.time() + 300}, private, algorithm="RS256", headers={"kid": kid})

    async def run():
        pool = {}
        good = token("old")
        results = await asyncio.gather(*(security._decode_jwks_token(good, project, client_pool=pool) for _ in range(50)))
        assert all(result["sub"] == "reader" for result in results)
        assert len(calls) == len(instances) == len(pool) == 1
        failures = await asyncio.gather(
            *(security._decode_jwks_token(token("unknown"), project, client_pool=pool) for _ in range(20)), return_exceptions=True
        )
        assert all(getattr(result, "status_code", None) == 401 for result in failures)
        assert len(calls) == 1
        key[:] = [{**public, "kid": "new", "alg": "RS256"}]
        for cache_key in security._jwks_refreshed:
            security._jwks_refreshed[cache_key] -= 6
        assert (await security._decode_jwks_token(token("new"), project, client_pool=pool))["sub"] == "reader"
        assert len(calls) == 2 and len(instances) == 1

    asyncio.run(run())


def test_passenger_concurrent_cold_requests_start_exactly_one_worker_loop():
    from framework.wsgi import PassengerApplication

    starts, closed, factories = [], [], []

    class App:
        async def start(self):
            starts.append(True)
            await asyncio.sleep(0.02)

        async def close(self):
            closed.append(True)

        async def __call__(self, scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": b'{"ok":true}'})

    def factory():
        factories.append(True)
        return App()

    bridge = PassengerApplication(factory)
    assert not factories and bridge._loop is None and bridge._thread is None
    barrier = threading.Barrier(6)

    def request():
        barrier.wait(timeout=3)
        with httpx.Client(transport=httpx.WSGITransport(app=bridge), base_url="http://test") as client:
            return client.get("/").json()

    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            assert list(pool.map(lambda _: request(), range(6))) == [{"ok": True}] * 6
        assert starts == factories == [True]
    finally:
        bridge.close()
    assert closed == [True] and not bridge._thread.is_alive()


def test_cancelled_synchronous_hook_cannot_stop_its_running_thread():
    from framework.factory import _invoke_hook

    entered, release, completed = threading.Event(), threading.Event(), threading.Event()

    def hook():
        entered.set()
        release.wait(timeout=2)
        completed.set()

    async def run():
        task = asyncio.create_task(_invoke_hook(hook))
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(0.001)
            assert entered.is_set()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not completed.is_set()
        finally:
            release.set()
        await asyncio.wait_for(asyncio.to_thread(completed.wait), 0.2)
        assert completed.is_set()

    asyncio.run(run())
