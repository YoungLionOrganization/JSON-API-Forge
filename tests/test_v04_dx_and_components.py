from __future__ import annotations

import asyncio
import io
import json
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile
from jsonschema import Draft202012Validator
from starlette.datastructures import Headers
from starlette.requests import Request

from framework.cache import CacheManager, MemoryTTLCache
from framework.cli import main as cli_main
from framework.config import DataSourceConfig, MediaConfig, MongoResourceConfig, ProjectConfig
from framework.datasources import DataSourceManager
from framework.events import EventHub, build_event_hub
from framework.media import (
    LocalMediaStore,
    _extension,
    _safe_name,
    build_media_store,
    make_signed_media_token,
    media_api_meta,
    save_media,
    verify_signed_media_token,
)
from framework.mongo import (
    _base_filter,
    _write_payload,
    count_documents,
    create_document,
    delete_document,
    list_documents,
    update_document,
)
from framework.security import Principal


def _request(query=b""):
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/x",
            "headers": [],
            "query_string": query,
            "server": ("test", 80),
            "client": ("127.0.0.1", 1),
            "scheme": "http",
        }
    )


def _project(tmp_path):
    return ProjectConfig.model_validate(
        {
            "slug": "demo",
            "name": "Demo",
            "databases": {"primary": {"url": "sqlite+aiosqlite:///demo.db"}},
            "security": {"jwt_enabled": False},
            "project_dir": str(tmp_path),
        }
    )


def test_cli_init_new_schema_and_no_default_secrets(tmp_path, capsys):
    (tmp_path / "app").mkdir()
    (tmp_path / "schemas").mkdir()
    cli_main(["--root", str(tmp_path), "new", "Bot", "--slug", "bot", "--preset", "discord-bot"])
    assert (tmp_path / "app/Bot/app.json").exists()
    assert (
        json.loads((tmp_path / "app/Bot/config/20-security.json").read_text())["security"]["bootstrap_admin_key"]
        == "$env:BOT_BOOTSTRAP_ADMIN_KEY"
    )
    manifest_schema = json.loads((tmp_path / "schemas/manifest.schema.json").read_text())
    fragment_schema = json.loads((tmp_path / "schemas/fragment.schema.json").read_text())
    Draft202012Validator(manifest_schema).validate(json.loads((tmp_path / "app/Bot/app.json").read_text()))
    for fragment in (tmp_path / "app/Bot/config").glob("*.json"):
        Draft202012Validator(fragment_schema).validate(json.loads(fragment.read_text()))
    cli_main(["--root", str(tmp_path), "init"])
    env = (tmp_path / ".env").read_text()
    assert "BOT_BOOTSTRAP_ADMIN_KEY=" in env and "change-me" not in env.lower()
    with pytest.raises(SystemExit):
        cli_main(["--root", str(tmp_path), "init"])
    cli_main(["--root", str(tmp_path), "schema"])
    assert json.loads((tmp_path / "schemas/project.schema.json").read_text())["$schema"].startswith("https://json-schema.org")
    manifest_schema = json.loads((tmp_path / "schemas/manifest.schema.json").read_text())
    assert "required" not in manifest_schema
    assert json.loads((tmp_path / "app/Bot/app.json").read_text())["$schema"] == "../../schemas/manifest.schema.json"


def test_datasources_file_read_query_and_mutations(tmp_path):
    async def run():
        manager = DataSourceManager(_project(tmp_path))
        (tmp_path / "items.json").write_text(
            json.dumps([{"id": 1, "name": "b", "kind": "x"}, {"id": 2, "name": "a", "kind": "x"}, {"id": 3, "name": "c", "kind": "y"}])
        )
        source = DataSourceConfig(
            name="items",
            type="json_file",
            file="items.json",
            public=True,
            public_write=True,
            writable=True,
            max_items=10,
            allowed_filters=["kind"],
            allowed_sort=["name"],
        )
        result = await manager.read(source, _request(b"kind=x&sort=name&limit=1"))
        assert result["total"] == 2 and result["items"][0]["name"] == "a"
        created = await manager.create(source, {"name": "d", "kind": "z"})
        assert created["id"] == 4
        with pytest.raises(HTTPException):
            await manager.update(source, "4", {"id": 999, "name": "e"})
        assert (await manager.update(source, "4", {"name": "e"}))["name"] == "e"
        assert await manager.delete(source, "4") == {"deleted": True}
        with pytest.raises(HTTPException):
            manager._file_path(DataSourceConfig(name="escape", type="json_file", file="../escape.json", public=True))
        static = DataSourceConfig(name="static", type="static", data={"ok": True}, public=True)
        assert await manager.read(static, _request()) == {"ok": True}
        await manager.close()

    asyncio.run(run())


def test_cache_singleflight_stale_and_generation():
    async def run():
        backend = MemoryTTLCache(max_entries=2)
        cache = CacheManager(backend)
        calls = 0

        async def loader():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return {"v": calls}

        results = await asyncio.gather(*[cache.get_or_set_json("same", 2, loader) for _ in range(8)])
        assert calls == 1 and all(v == {"v": 1} for v, _ in results)
        assert cache._locks == {}
        await cache.set_json("stale", {"old": True}, ttl=1, stale_ttl=3)
        raw = json.loads((await backend.get("stale")).decode())
        raw["fresh_until"] = time.time() - 1
        await backend.set("stale", json.dumps(raw).encode(), 3)
        value, hit = await cache.get_or_set_json("stale", 2, loader, stale_ttl=3)
        assert value == {"old": True} and hit
        await asyncio.sleep(0.04)
        assert (await cache.get_json("stale"))["v"] >= 2
        assert await cache.invalidate_namespace("n") == 1
        await cache.close()

    asyncio.run(run())


def test_media_local_store_and_signed_token(tmp_path):
    async def run():
        store = LocalMediaStore(str(tmp_path / "media"))
        assert _safe_name("../../hello world?.png") == "hello_world_.png" and _extension("A.JPEG") == "jpeg"
        with pytest.raises(HTTPException):
            store.path_for("../escape")
        up = UploadFile(io.BytesIO(b"abc123"), filename="hello.txt", headers=Headers({"content-type": "text/plain"}))
        size, digest = await store.save_upload(up, "demo/file.txt", 10)
        assert size == 6 and len(digest) == 64
        store.delete("demo/file.txt")
        too = UploadFile(io.BytesIO(b"0123456789"), filename="x.bin")
        with pytest.raises(HTTPException):
            await store.save_upload(too, "demo/big.bin", 5)
        assert isinstance(build_media_store(MediaConfig(enabled=True, backend="local", local_directory=str(tmp_path))), LocalMediaStore)
        safe = media_api_meta(
            {
                "id": "m1",
                "original_name": "a.txt",
                "content_type": "text/plain",
                "size": 1,
                "sha256": "abc",
                "created_at": "now",
                "owner_subject": "x",
                "storage_key": "internal",
            }
        )
        assert "storage_key" not in safe and "owner_subject" not in safe

        class BadConn:
            async def execute(self, *a, **k):
                raise RuntimeError("metadata database unavailable")

        class BadCtx:
            async def __aenter__(self):
                return BadConn()

            async def __aexit__(self, *a):
                return False

        class BadEngine:
            def begin(self):
                return BadCtx()

        failed = UploadFile(io.BytesIO(b"orphan-check"), filename="orphan.txt", headers=Headers({"content-type": "text/plain"}))
        with pytest.raises(RuntimeError):
            await save_media(
                engine=BadEngine(),
                store=store,
                project_slug="demo",
                config=MediaConfig(enabled=True, allowed_mime_types=["text/plain"], deduplicate=False),
                upload=failed,
                owner_subject="owner",
            )

    asyncio.run(run())
    secret = "s" * 64
    token = make_signed_media_token("demo", "id", 60, secret=secret)
    assert verify_signed_media_token("demo", "id", token, secret=secret) and not verify_signed_media_token(
        "demo", "other", token, secret=secret
    )


class FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def skip(self, n):
        self.rows = self.rows[n:]
        return self

    def limit(self, n):
        self.rows = self.rows[:n]
        return self

    def sort(self, field, direction=None):
        specs = field if isinstance(field, list) else [(field, direction)]
        for key, order in reversed(specs):
            self.rows.sort(key=lambda x: x.get(key), reverse=order < 0)
        return self

    async def to_list(self, length):
        return self.rows[:length]


class FakeCollection:
    def __init__(self, rows):
        self.rows = [dict(x) for x in rows]
        self.next_id = 100

    @staticmethod
    def _match(row, filt):
        for k, e in filt.items():
            a = row.get(k)
            if isinstance(e, dict):
                for op, v in e.items():
                    if op == "$in" and a not in v:
                        return False
            elif a != e:
                return False
        return True

    def find(self, f):
        return FakeCursor([r for r in self.rows if self._match(r, f)])

    async def count_documents(self, f):
        return len([r for r in self.rows if self._match(r, f)])

    async def find_one(self, f):
        return next((dict(r) for r in self.rows if self._match(r, f)), None)

    async def insert_one(self, data):
        self.next_id += 1
        row = {"_id": str(self.next_id), **data}
        self.rows.append(row)
        return SimpleNamespace(inserted_id=str(self.next_id))

    async def update_one(self, f, u):
        for r in self.rows:
            if self._match(r, f):
                r.update(u["$set"])
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)

    async def delete_one(self, f):
        for i, r in enumerate(self.rows):
            if self._match(r, f):
                self.rows.pop(i)
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)


def test_mongo_resource_logic_with_fake_async_database():
    async def run():
        resource = MongoResourceConfig.model_validate(
            {
                "database": "main",
                "collection": "profiles",
                "path": "profiles",
                "tenant_field": "tenant_id",
                "soft_delete_field": "deleted_at",
                "allowed_filters": ["name"],
                "allowed_sort": ["name"],
                "filter_operators": ["eq", "in"],
                "writable_fields": ["name"],
            }
        )
        p = Principal(kind="api_key", subject="a", roles=set(), permissions=set(), tenant_id="t1")
        db = {
            "profiles": FakeCollection(
                [
                    {"_id": "1", "name": "b", "tenant_id": "t1", "deleted_at": None},
                    {"_id": "2", "name": "a", "tenant_id": "t1", "deleted_at": None},
                    {"_id": "3", "name": "z", "tenant_id": "t2", "deleted_at": None},
                ]
            )
        }
        listed = await list_documents(_request(b"sort=name&name__in=a,b"), db, resource, p)
        assert [x["name"] for x in listed["items"]] == ["a", "b"] and (await count_documents(_request(), db, resource, p))["count"] == 2
        created = await create_document(db, resource, p, {"name": "c"})
        assert created["tenant_id"] == "t1"
        assert (await update_document(db, resource, p, created["_id"], {"name": "d"}))["name"] == "d"
        assert await delete_document(db, resource, p, created["_id"]) == {"deleted": True}
        with pytest.raises(HTTPException):
            _write_payload({"not_allowed": 1}, resource, None)
        with pytest.raises(HTTPException):
            _base_filter(resource, Principal(kind="anonymous", subject="x", roles=set(), permissions=set()))

    asyncio.run(run())


def test_event_hub_delivery_and_cleanup():
    class WS:
        def __init__(self, fail=False):
            self.fail = fail
            self.sent = []

        async def accept(self):
            pass

        async def send_json(self, event):
            if self.fail:
                raise RuntimeError("dead")
            self.sent.append(event)

    async def run():
        hub = EventHub()
        ws, dead = WS(), WS(True)
        await hub.connect_ws("c", ws)
        await hub.connect_ws("c", dead)
        assert await hub.publish("c", {"x": 1}) == 2
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert ws.sent == [{"x": 1}]
        await hub.disconnect_ws("c", ws)
        assert await hub.ping()
        assert isinstance(build_event_hub("memory", None, "forge", "p"), EventHub)
        with pytest.raises(RuntimeError):
            build_event_hub("redis", None, "forge", "p")

    asyncio.run(run())
