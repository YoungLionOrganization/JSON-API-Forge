from __future__ import annotations

import asyncio
import json

import httpx
from fastapi.testclient import TestClient

from framework.config import load_config_resilient
from framework.reload import ReloadingForge, configuration_signature
from framework.settings import settings


def write_app(root, name, slug, value, *, path="info"):
    directory = root / name
    directory.mkdir(exist_ok=True)
    payload = {
        "name": name,
        "slug": slug,
        "databases": {"primary": {"url": f"sqlite+aiosqlite:///{directory}/app.db"}},
        "security": {"jwt_enabled": False},
        "data_sources": [{"name": "info", "path": path, "type": "static", "public": True, "data": {"value": value}}],
    }
    (directory / "app.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


def configure(tmp_path, monkeypatch):
    root = tmp_path / "app"
    root.mkdir()
    monkeypatch.setattr(settings, "apps_dir", root)
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "apps_auto_reload", False)
    monkeypatch.setattr(settings, "editor_api_enabled", False)
    monkeypatch.setattr(settings, "internal_database_url", f"sqlite+aiosqlite:///{tmp_path}/internal.db")
    monkeypatch.setattr(settings, "jwt_secret", "reload-test" * 8)
    return root


def test_atomic_reload_add_change_remove_and_invalid_edit(tmp_path, monkeypatch):
    root = configure(tmp_path, monkeypatch)
    write_app(root, "Alpha", "alpha", 1)

    async def run():
        live = ReloadingForge(root)
        await live.start()
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=live), base_url="http://testserver") as client:
                alpha = live.state.runtimes["alpha"]
                assert (await client.get("/api/alpha/v1/info")).json() == {"value": 1}
                write_app(root, "Beta", "beta", 2)
                assert await live.reload_now()
                assert live.state.runtimes["alpha"] is alpha
                assert (await client.get("/api/beta/v1/info")).json() == {"value": 2}
                write_app(root, "Alpha", "alpha", 3, path="changed")
                assert await live.reload_now()
                assert (await client.get("/api/alpha/v1/info")).status_code == 404
                assert (await client.get("/api/alpha/v1/changed")).json() == {"value": 3}
                (root / "Alpha/app.json").write_text('{"secret":"must-not-appear', encoding="utf-8")
                write_app(root, "Beta", "beta", 4)
                assert await live.reload_now()
                assert (await client.get("/api/alpha/v1/changed")).json() == {"value": 3}
                assert (await client.get("/api/beta/v1/info")).json() == {"value": 4}
                (root / "Beta/app.json").unlink()
                assert await live.reload_now()
                assert (await client.get("/api/beta/v1/info")).status_code == 404
                assert not await live.reload_now()
                routes = live.current.app.router.routes
                keys = [(method, route.path) for route in routes for method in getattr(route, "methods", ["WS"])]
                assert len(keys) == len(set(keys))
        finally:
            await live.close()

    asyncio.run(run())


def test_failed_runtime_edit_keeps_healthy_app_and_accepts_other_app(tmp_path, monkeypatch):
    root = configure(tmp_path, monkeypatch)
    write_app(root, "Alpha", "alpha", 1)

    async def run():
        live = ReloadingForge(root)
        await live.start()
        try:
            payload = write_app(root, "Alpha", "renamed", 8)
            payload["databases"]["primary"]["url"] = "missing-driver://invalid"
            (root / "Alpha/app.json").write_text(json.dumps(payload))
            write_app(root, "Beta", "beta", 2)
            assert await live.reload_now()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=live), base_url="http://testserver") as client:
                assert (await client.get("/api/alpha/v1/info")).json() == {"value": 1}
                assert (await client.get("/api/beta/v1/info")).json() == {"value": 2}
        finally:
            await live.close()

    asyncio.run(run())


def test_old_generation_drains_and_pending_changes_are_bounded(tmp_path, monkeypatch):
    root = configure(tmp_path, monkeypatch)
    write_app(root, "Alpha", "alpha", 1)

    async def run():
        live = ReloadingForge(root)
        await live.start()
        released = asyncio.Event()
        entered = asyncio.Event()
        old = live.current

        @old.app.get("/slow")
        async def slow():
            entered.set()
            await released.wait()
            assert old.app.state.runtimes["alpha"].available
            return {"old": True}

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=live), base_url="http://testserver") as client:
            request = asyncio.create_task(client.get("/slow"))
            await entered.wait()
            write_app(root, "Alpha", "alpha", 2)
            assert await live.reload_now()
            assert old.context is not None
            assert (await client.get("/api/alpha/v1/info")).json() == {"value": 2}
            released.set()
            assert (await request).json() == {"old": True}
        await live.close()
        assert old.context is None

    asyncio.run(run())


def test_conflicts_quarantine_all_owners_and_reserved_prefixes(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    write_app(root, "A", "same", 1)
    write_app(root, "B", "same", 2)
    write_app(root, "Healthy", "healthy", 3)
    config, issues = load_config_resilient(root)
    assert [project.slug for project in config.projects] == ["healthy"]
    assert issues == {"A": "RouteOwnershipConflict", "B": "RouteOwnershipConflict"}
    payload = write_app(root, "Reserved", "reserved", 1)
    payload["api_prefix"] = "/__forge/editor"
    (root / "Reserved/app.json").write_text(json.dumps(payload))
    assert "Reserved" in load_config_resilient(root)[1]


def test_watcher_ignores_data_and_rejects_oversize_config(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    write_app(root, "Alpha", "alpha", 1)
    signature = configuration_signature(root)
    (root / "Alpha/data").mkdir()
    (root / "Alpha/data/state.json").write_text('{"value":2}')
    assert configuration_signature(root) == signature
    (root / "Alpha/app.json").write_bytes(b" " * (2 * 1024 * 1024 + 1))
    assert "Alpha" in load_config_resilient(root)[1]


def test_watcher_excludes_links_and_bounds_configuration_fanout(tmp_path):
    import pytest

    root = tmp_path / "app"
    root.mkdir()
    write_app(root, "Alpha", "alpha", 1)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "_ignored").mkdir()
    (root / "linked").symlink_to(outside, target_is_directory=True)
    fragments = root / "Alpha/config"
    fragments.symlink_to(outside, target_is_directory=True)
    signature = configuration_signature(root)
    (outside / "untrusted.json").write_text("{}")
    assert configuration_signature(root) == signature
    assert "Alpha" in load_config_resilient(root)[1]
    fragments.unlink()
    fragments.mkdir()
    for number in range(257):
        (fragments / f"{number:03}.json").write_text("{}")
    with pytest.raises(ValueError, match="fragments"):
        configuration_signature(root)
    assert "Alpha" in load_config_resilient(root)[1]


def test_live_lifespan_and_debounced_background_watch(tmp_path, monkeypatch):
    root = configure(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "apps_auto_reload", True)
    monkeypatch.setattr(settings, "apps_reload_interval_seconds", 0.1)
    monkeypatch.setattr(settings, "apps_reload_debounce_seconds", 0.1)
    write_app(root, "Alpha", "alpha", 1)
    live = ReloadingForge(root)
    with TestClient(live) as client:
        assert client.get("/api/alpha/v1/info").json() == {"value": 1}
        (root / "Alpha/app.json").write_text('{"unfinished":')
        assert client.get("/api/alpha/v1/info").json() == {"value": 1}
        write_app(root, "Alpha", "alpha", 2)

        async def changed():
            while live.generation == 0:
                await asyncio.sleep(0.025)

        client.portal.call(lambda: asyncio.wait_for(changed(), timeout=3))
        assert client.get("/api/alpha/v1/info").json() == {"value": 2}
        assert live.last_error is None
    assert live._watcher is None
    assert live.current.context is None


def test_reused_runtime_survives_repeated_owner_cleanup(tmp_path, monkeypatch):
    from framework.runtime import RuntimeManager

    root = configure(tmp_path, monkeypatch)
    write_app(root, "Alpha", "alpha", 1)
    config, _ = load_config_resilient(root)

    async def run():
        owner = RuntimeManager(config)
        await owner.start()
        runtime = owner.runtimes["alpha"]
        borrower = RuntimeManager(config, reuse=owner.runtimes)
        await borrower.start()
        await owner.close()
        await owner.close()
        assert runtime.available and runtime._leases == 1
        await borrower.close()
        assert not runtime.available and runtime._leases == 0

    asyncio.run(run())


def test_route_and_operation_id_collisions_are_rejected():
    import pytest
    from fastapi import FastAPI

    from framework.factory import _check_route_integrity

    for paths, ids in [(("/items/{id}", "/items/{name}"), ("one", "two")), (("/a", "/b"), ("same", "same"))]:
        app = FastAPI()
        for path, operation in zip(paths, ids, strict=True):
            app.add_api_route(path, lambda: {}, methods=["GET"], operation_id=operation)
        with pytest.raises(ValueError, match="Duplicate generated"):
            _check_route_integrity(app.router.routes)


def test_passenger_starts_and_closes_http_services_on_its_loop(tmp_path, monkeypatch):
    from framework.wsgi import PassengerApplication

    root = configure(tmp_path, monkeypatch)
    write_app(root, "Alpha", "alpha", 1)
    live = ReloadingForge(root)
    bridge = PassengerApplication(lambda: live)
    try:
        with httpx.Client(transport=httpx.WSGITransport(app=bridge), base_url="http://testserver") as client:
            assert client.get("/api/alpha/v1/info").json() == {"value": 1}
            assert client.get("/ready").status_code == 200
    finally:
        bridge.close()
        bridge.close()
    assert live.current.context is None
    assert not bridge._thread.is_alive()


def test_reload_rejects_identity_takeover_and_unstable_candidate(tmp_path, monkeypatch):
    root = configure(tmp_path, monkeypatch)
    write_app(root, "Alpha", "alpha", 1)
    write_app(root, "Beta", "beta", 2)

    async def run():
        live = ReloadingForge(root)
        await live.start()
        original = live.current
        try:
            write_app(root, "Alpha", "beta", 9)
            (root / "Beta/app.json").write_text('{"broken":')
            assert not await live.reload_now()
            assert live.last_error == "ValueError"
            assert live.current is original
            write_app(root, "Alpha", "alpha", 3)
            write_app(root, "Beta", "beta", 4)
            build = live._build
            candidates = []

            def unstable(*args):
                candidate = build(*args)
                start = candidate.start

                async def start_then_edit():
                    await start()
                    write_app(root, "Alpha", "alpha", 5)

                candidate.start = start_then_edit
                candidates.append(candidate)
                return candidate

            monkeypatch.setattr(live, "_build", unstable)
            assert not await live.reload_now()
            assert candidates[0].context is None
            assert live.current is original
            monkeypatch.setattr(live, "_build", build)
            assert await live.reload_now()
        finally:
            await live.close()

    asyncio.run(run())


def test_reload_cancellation_cleans_candidate_and_keeps_live_services(tmp_path, monkeypatch):
    import pytest

    root = configure(tmp_path, monkeypatch)
    write_app(root, "Alpha", "alpha", 1)

    async def run():
        live = ReloadingForge(root)
        await live.start()
        original = live.current
        entered = asyncio.Event()
        build = live._build
        candidates = []

        def blocking(*args):
            candidate = build(*args)
            start = candidate.start

            async def start_then_block():
                await start()
                entered.set()
                await asyncio.Future()

            candidate.start = start_then_block
            candidates.append(candidate)
            return candidate

        try:
            monkeypatch.setattr(live, "_build", blocking)
            write_app(root, "Alpha", "alpha", 2)
            task = asyncio.create_task(live.reload_now())
            await asyncio.wait_for(entered.wait(), 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert candidates[0].context is None
            assert live.current is original
            assert live.state.runtimes["alpha"].available
        finally:
            await live.close()

    asyncio.run(run())


def test_long_streams_bound_retired_generations(tmp_path, monkeypatch):
    root = configure(tmp_path, monkeypatch)
    write_app(root, "Alpha", "alpha", 0)

    async def run():
        live = ReloadingForge(root)
        await live.start()
        release = asyncio.Event()
        requests = []
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=live), base_url="http://testserver") as client:
            try:
                for number in (1, 2):
                    entered = asyncio.Event()

                    async def slow(signal=entered):
                        signal.set()
                        await release.wait()
                        return {"ok": True}

                    # Hide the closure parameter from FastAPI's schema.
                    slow.__signature__ = __import__("inspect").Signature()
                    live.current.app.get("/slow")(slow)
                    requests.append(asyncio.create_task(client.get("/slow")))
                    await asyncio.wait_for(entered.wait(), 3)
                    write_app(root, "Alpha", "alpha", number)
                    assert await live.reload_now()
                write_app(root, "Alpha", "alpha", 3)
                assert not await live.reload_now()
                assert len(live._retired) == 2
                release.set()
                assert all(response.status_code == 200 for response in await asyncio.gather(*requests))
                assert await live.reload_now()
                assert (await client.get("/api/alpha/v1/info")).json() == {"value": 3}
            finally:
                release.set()
                await asyncio.gather(*requests)
                await live.close()

    asyncio.run(run())
