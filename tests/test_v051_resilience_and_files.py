from __future__ import annotations

import hashlib
import io
import json
from argparse import Namespace

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient

from framework.config import DataSourceConfig, ProjectConfig, load_config, load_config_resilient
from framework.datasources import DataSourceManager
from framework.file_safety import relative_parts
from framework.media import LocalMediaStore
from framework.settings import settings


def app_file(apps, name, **overrides):
    directory = apps / name
    directory.mkdir(parents=True)
    value = {
        "slug": name.lower(),
        "name": name,
        "api_prefix": f"/api/{name.lower()}",
        "databases": {"primary": {"url": "sqlite+aiosqlite:///:memory:"}},
        "security": {"jwt_enabled": False},
        "data_sources": [{"name": "ping", "path": "ping", "type": "static", "public": True, "data": {"ok": True}}],
        **overrides,
    }
    (directory / "app.json").write_text(json.dumps(value), encoding="utf-8")
    return directory


def server_settings(monkeypatch):
    monkeypatch.setattr(settings, "internal_database_url", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setattr(settings, "internal_schema_mode", "create")
    monkeypatch.setattr(settings, "editor_api_enabled", False)
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "operator_token", "test-operator")


def test_bad_app_config_and_route_registration_do_not_stop_other_apps(tmp_path, monkeypatch):
    import framework.factory as factory

    apps = tmp_path / "app"
    app_file(apps, "Good")
    broken = app_file(apps, "Broken")
    (broken / "app.json").write_text('{"password":"do-not-log-this",', encoding="utf-8")
    app_file(apps, "Conflict", slug="good", api_prefix="/api/good/nested")
    app_file(apps, "Independent")
    app_file(apps, "Disabled", enabled=False)
    app_file(apps, "_Ignored")
    config, issues = load_config_resilient(apps)
    assert len(config.projects) == 1
    assert config.projects[0].slug == "independent"
    assert set(issues) == {"Broken", "Good", "Conflict"}
    assert "do-not-log-this" not in str(issues)
    with pytest.raises((RuntimeError, ValueError)):
        load_config(apps)
    app_file(apps, "Routebad")
    server_settings(monkeypatch)
    register = factory.register_project_routes

    def fail_one(app, runtime, *args, **kwargs):
        if runtime.config.slug == "routebad":
            app.get("/should-be-removed")(lambda: {"partial": True})
            raise RuntimeError("route construction failed")
        return register(app=app, runtime=runtime, **kwargs)

    monkeypatch.setattr(factory, "register_project_routes", fail_one)
    with TestClient(factory.create_app(apps_dir=apps)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/independent/ping").json() == {"ok": True}
        assert client.get("/api/good/nested/ping").status_code == 404
        assert client.get("/api/routebad/ping").status_code == 503
        assert client.get("/should-be-removed").status_code == 404
        assert client.get("/ready").json() == {"status": "degraded"}
        details = client.get("/ready", headers={"X-Forge-Operator-Token": "test-operator"}).json()
        assert details["projects"]["routebad"]["error_type"] == "RuntimeError"
        assert set(details["configuration_errors"]) == {"Broken", "Good", "Conflict"}


def test_dev_reload_restarts_python_hooks_while_live_factory_watches_json(tmp_path, monkeypatch):
    import uvicorn

    from framework.cli import cmd_dev

    (tmp_path / "app").mkdir()
    captured = {}
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: captured.update(args=args, **kwargs))
    cmd_dev(Namespace(root=str(tmp_path), reload=True, host="127.0.0.1", port=8000, log_level="warning"))
    assert captured["reload_dirs"] == [str(tmp_path / "app")]
    assert captured["args"] == ("framework.reload:create_live_app",)
    assert captured["reload_includes"] == ["*.py"]
    assert {"*/data/*", "*/media/*", "*.db*"} <= set(captured["reload_excludes"])


def test_startup_error_and_request_exception_are_isolated(tmp_path, monkeypatch):
    import framework.runtime as runtime
    from framework.factory import create_app

    apps = tmp_path / "app"
    app_file(apps, "Good")
    app_file(apps, "Bad")
    server_settings(monkeypatch)
    build = runtime.build_registry

    async def fail_one(project):
        if project.slug == "bad":
            raise RuntimeError("one app failed")
        return await build(project)

    monkeypatch.setattr(runtime, "build_registry", fail_one)
    app = create_app(apps_dir=apps)

    @app.get("/api/good/explode")
    def explode():
        raise RuntimeError("one request failed")

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/api/bad/ping").status_code == 503
        assert client.get("/api/good/explode").status_code == 500
        assert client.get("/api/good/ping").status_code == 200
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 503


@pytest.mark.parametrize("path", ["", "/abs", "a/../b", "a/./b", "a//b", "C:escape", "a\\b", "NUL.txt", "a\nfile", "trailing."])
def test_portable_file_paths_reject_ambiguous_names(path):
    with pytest.raises(HTTPException):
        relative_parts(path)


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
async def test_confined_data_and_atomic_media(tmp_path, monkeypatch, fallback):
    if fallback:
        monkeypatch.setattr("framework.file_safety.descriptor_paths_supported", lambda: False)
    project = ProjectConfig(slug="p", name="P", project_dir=str(tmp_path), databases={"primary": {"url": "sqlite+aiosqlite:///:memory:"}})
    manager = DataSourceManager(project)
    source = DataSourceConfig(
        name="records", type="json_file", file="nested/data.json", public=True, writable=True, write_permission="data.write"
    )
    try:
        manager._write_file_sync(source, [{"id": 1}])
        assert manager._read_file_sync(source) == [{"id": 1}]
        manager._write_file_sync(source, [{"id": 2}])
        assert manager._read_file_sync(source) == [{"id": 2}]
        outside = tmp_path.parent / (tmp_path.name + "-outside")
        outside.mkdir()
        (outside / "data.json").write_text("[]", encoding="utf-8")
        (tmp_path / "linked").symlink_to(outside, target_is_directory=True)
        unsafe = source.model_copy(update={"file": "linked/data.json"})
        with pytest.raises(HTTPException):
            manager._read_file_sync(unsafe)
        with pytest.raises(HTTPException):
            manager._write_file_sync(unsafe, ["escaped"])
        assert (outside / "data.json").read_text() == "[]"
        store = LocalMediaStore(str(tmp_path / "media"))
        upload = UploadFile(file=io.BytesIO(b"complete"), filename="file.bin")
        key = "app/2026/file.bin"
        assert await store.save_upload(upload, key, 16) == (8, hashlib.sha256(b"complete").hexdigest())
        assert upload.file.closed
        with pytest.raises(HTTPException) as error:
            await store.save_upload(UploadFile(file=io.BytesIO(b"replace")), key, 16)
        assert error.value.status_code == 409
        assert store.path_for(key).read_bytes() == b"complete"
        with pytest.raises(HTTPException) as error:
            await store.save_upload(UploadFile(file=io.BytesIO(b"too large")), "app/partial.bin", 2)
        assert error.value.status_code == 413
        assert not (store.root / "app/partial.bin").exists()
        assert not list(store.root.rglob("*.tmp"))
        store.delete(key)
        store.delete(key)
        store.delete("missing/file.bin")
    finally:
        await manager.close()
