from __future__ import annotations

import asyncio
import sys
import types
from datetime import timezone

import pytest
from sqlalchemy import Column, Integer, MetaData, Table

from framework.config import ProjectConfig
from framework.events import EventHub, RedisEventHub, build_event_hub, sse_encode


def test_event_hub_and_redis_paths(monkeypatch):
    class WS:
        def __init__(self, fail=False):
            self.fail = fail
            self.accepted = False
            self.sent = []

        async def accept(self):
            self.accepted = True

        async def send_json(self, p):
            if self.fail:
                raise RuntimeError("closed")
            self.sent.append(p)

    async def memory():
        hub = EventHub()
        live = WS()
        dead = WS(True)
        await hub.connect_ws("u", live)
        await hub.connect_ws("u", dead)
        sub = hub.subscribe("u", 1)
        waiter = asyncio.create_task(anext(sub))
        await asyncio.sleep(0)
        assert await hub.publish("u", {"n": 1}) == 3
        assert await waiter == {"n": 1}
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await hub.publish("u", {"n": 2})
        await hub.publish("u", {"n": 3})
        assert await anext(sub) == {"n": 2}
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert all(c.websocket is not dead for c in hub._websockets["u"])
        await sub.aclose()
        await hub.disconnect_ws("u", live)
        assert await hub.ping()
        await hub.close()

    asyncio.run(memory())
    assert sse_encode({"x": object()}).startswith(b"data: ")

    published = []

    class PubSub:
        def __init__(self):
            self.messages = asyncio.Queue()
            self.closed = False

        async def subscribe(self, c):
            self.channel = c

        async def unsubscribe(self, c):
            self.unsub = c

        async def aclose(self):
            self.closed = True

        async def listen(self):
            while True:
                yield await self.messages.get()

    class Redis:
        def __init__(self):
            self.ps = PubSub()
            self.closed = False

        def pubsub(self):
            return self.ps

        async def publish(self, c, p):
            published.append((c, p))
            return 4

        async def ping(self):
            return True

        async def aclose(self):
            self.closed = True

    fake = Redis()
    ra = types.ModuleType("redis.asyncio")
    ra.from_url = lambda *a, **k: fake
    rp = types.ModuleType("redis")
    rp.asyncio = ra
    monkeypatch.setitem(sys.modules, "redis", rp)
    monkeypatch.setitem(sys.modules, "redis.asyncio", ra)

    async def redis_flow():
        hub = RedisEventHub("redis://fake", "forge", "app")
        sub = hub.subscribe("updates", 4)
        first = asyncio.create_task(anext(sub))
        for _ in range(20):
            if hub._subscribers["updates"]:
                break
            await asyncio.sleep(0)
        await fake.ps.messages.put({"type": "message", "data": '{"ok":true}'})
        assert await asyncio.wait_for(first, 1) == {"ok": True}
        second = asyncio.create_task(anext(sub))
        await fake.ps.messages.put({"type": "message", "data": "plain"})
        assert await asyncio.wait_for(second, 1) == {"event": "plain"}
        assert await hub.publish("updates", {"x": 1}) == 4 and await hub.ping()
        await sub.aclose()
        await hub.close()
        assert fake.closed and fake.ps.closed

    asyncio.run(redis_flow())
    assert isinstance(build_event_hub("memory", None, "f", "p"), EventHub)
    with pytest.raises(RuntimeError):
        build_event_hub("redis", None, "f", "p")


def test_database_registry_paths(monkeypatch, tmp_path):
    import framework.db as db

    calls = {"idem": [], "dispose": [], "created": 0, "reflect": []}

    class Conn:
        async def run_sync(self, fn):
            calls["created"] += 1

    class Ctx:
        async def __aenter__(self):
            return Conn()

        async def __aexit__(self, *a):
            return False

    class Engine:
        def __init__(self, url):
            self.url = url

        def begin(self):
            return Ctx()

        async def dispose(self):
            calls["dispose"].append(self.url)

    monkeypatch.setattr(db, "create_async_engine", lambda url, **kw: Engine(url))

    async def idem(engine, mode="create"):
        calls["idem"].append(engine.url)

    async def reflect(engine, metadata, table_name):
        calls["reflect"].append((engine.url, table_name))
        return Table(table_name, metadata, Column("id", Integer, primary_key=True), extend_existing=True)

    monkeypatch.setattr(db, "init_operation_idempotency", idem)
    monkeypatch.setattr(db, "_reflect_table", reflect)
    sqlite = tmp_path / "nested/db.sqlite"
    p = ProjectConfig.model_validate(
        {
            "slug": "p",
            "name": "P",
            "security": {"jwt_enabled": False},
            "databases": {"primary": {"url": f"sqlite+aiosqlite:///{sqlite}"}, "archive": {"url": "postgresql+asyncpg://u:p@db/a"}},
            "resources": [
                {
                    "database": "primary",
                    "table": "items",
                    "path": "items",
                    "auto_create": True,
                    "columns": {"id": {"type": "integer", "primary_key": True, "nullable": False}},
                },
                {"database": "archive", "table": "history", "path": "history", "auto_create": False},
            ],
            "operations": [
                {
                    "name": "write",
                    "database": "primary",
                    "permission": "w",
                    "transaction": True,
                    "idempotency": True,
                    "statements": [{"sql": "UPDATE items SET id=id"}],
                }
            ],
        }
    )

    async def run():
        reg = await db.build_registry(p)
        assert set(reg.engines) == {"primary", "archive"}
        assert ("primary", "items") in reg.tables and ("archive", "history") in reg.tables
        assert calls["created"] == 1 and calls["idem"]
        await reg.dispose()
        assert len(calls["dispose"]) == 2

    asyncio.run(run())
    assert sqlite.parent.exists()
    bad = ProjectConfig.model_validate(
        {
            "slug": "b",
            "name": "B",
            "security": {"jwt_enabled": False},
            "databases": {"primary": {"url": "sqlite+aiosqlite:///:memory:"}},
            "resources": [{"database": "missing", "table": "x", "path": "x"}],
        }
    )
    with pytest.raises(RuntimeError):
        asyncio.run(db.build_registry(bad))


def test_cache_backends_and_failure_policy(monkeypatch):
    import framework.cache as c

    class Lock:
        async def acquire(self):
            return True

        async def release(self):
            self.released = True

    class Redis:
        def __init__(self):
            self.data = {}
            self.closed = False

        async def get(self, k):
            return self.data.get(k)

        async def set(self, k, v, ex=None):
            self.data[k] = v

        async def delete(self, k):
            self.data.pop(k, None)

        async def incr(self, k):
            self.data[k] = int(self.data.get(k, 0)) + 1
            return self.data[k]

        def lock(self, *a, **k):
            return Lock()

        async def ping(self):
            return True

        async def aclose(self):
            self.closed = True

    fake = Redis()
    ra = types.ModuleType("redis.asyncio")
    ra.from_url = lambda *a, **k: fake
    rp = types.ModuleType("redis")
    rp.asyncio = ra
    monkeypatch.setitem(sys.modules, "redis", rp)
    monkeypatch.setitem(sys.modules, "redis.asyncio", ra)

    async def run():
        mem = c.MemoryTTLCache(max_entries=2)
        await mem.set("a", b"1", 30)
        await mem.set("b", b"2", 30)
        assert await mem.get("a") == b"1"
        await mem.set("c", b"3", 30)
        assert await mem.get("b") is None
        await mem.delete("c")
        assert await mem.bump_generation("n") == 1 and await mem.generation("n") == 1 and await mem.ping()
        red = c.RedisTTLCache("redis://fake", "p")
        await red.set("x", b"v", 0)
        assert await red.get("x") == b"v"
        assert await red.generation("n") == 0
        assert await red.bump_generation("n") == 1
        lock = red.distributed_lock("x", timeout=1, blocking_timeout=1)
        assert await lock.acquire()
        await lock.release()
        assert await red.ping()
        tier = c.TieredCache(c.MemoryTTLCache(10), red)
        await red.set("l2", b"value", 10)
        assert await tier.get("l2") == b"value"
        await tier.set("both", b"v", 20)
        assert await tier.get("both") == b"v"
        assert await tier.bump_generation("t") == 1 and await tier.generation("t") == 1
        await tier.delete("both")

        class Broken:
            async def generation(self, n):
                raise RuntimeError("down")

            async def get(self, k):
                raise RuntimeError("down")

            async def set(self, k, v, t):
                raise RuntimeError("down")

            async def bump_generation(self, n):
                raise RuntimeError("down")

            async def close(self):
                pass

        openm = c.CacheManager(Broken(), fail_open=True)
        assert await openm.generation("x") is None and await openm.get_json("x") is None
        await openm.set_json("x", {}, 1)
        assert await openm.invalidate_namespace("x") == -1 and await openm.ping()
        await openm.close()
        closed = c.CacheManager(Broken(), fail_open=False)
        with pytest.raises(RuntimeError):
            await closed.get_json("x")
        with pytest.raises(RuntimeError):
            await closed.set_json("x", {}, 1)
        with pytest.raises(RuntimeError):
            await closed.invalidate_namespace("x")
        await tier.close()
        assert fake.closed

    asyncio.run(run())


def test_media_policy_and_signed_edges(tmp_path):
    import hashlib
    import io
    from datetime import datetime

    from fastapi import HTTPException, UploadFile
    from starlette.datastructures import Headers

    import framework.media as m
    from framework.config import MediaConfig

    class MR:
        def __init__(self, row=None, scalar=0, rowcount=1):
            self.row = row
            self.scalarv = scalar
            self.rowcount = rowcount

        def mappings(self):
            return self

        def first(self):
            return self.row

        def scalar_one(self):
            return self.scalarv

    class Conn:
        def __init__(self, e):
            self.e = e

        async def execute(self, stmt, *a, **k):
            s = str(stmt)
            if "sum(" in s.lower():
                return MR(scalar=0)
            if s.lstrip().startswith("SELECT"):
                return MR(row=self.e.row)
            if s.lstrip().startswith("UPDATE"):
                return MR(rowcount=self.e.rc)
            if s.lstrip().startswith("INSERT"):
                self.e.inserted += 1
                return MR()
            if s.lstrip().startswith("DELETE"):
                self.e.deleted += 1
                return MR()
            return MR()

    class Ctx:
        def __init__(self, e):
            self.e = e

        async def __aenter__(self):
            return Conn(self.e)

        async def __aexit__(self, *a):
            return False

    class E:
        def __init__(self, row=None, rc=1):
            self.row = row
            self.rc = rc
            self.inserted = 0
            self.deleted = 0

        def connect(self):
            return Ctx(self)

        def begin(self):
            return Ctx(self)

    def upload(data=b"abc", name="x.txt", ct="text/plain"):
        return UploadFile(io.BytesIO(data), filename=name, headers=Headers({"content-type": ct}))

    async def run():
        store = m.LocalMediaStore(str(tmp_path / "media"))
        cfg = MediaConfig(enabled=True, allowed_mime_types=["image/png"], allowed_extensions=["png"])
        with pytest.raises(HTTPException):
            await m.save_media(engine=E(), store=store, project_slug="p", config=cfg, upload=upload(ct="text/plain"), owner_subject="u")
        with pytest.raises(HTTPException):
            await m.save_media(
                engine=E(), store=store, project_slug="p", config=cfg, upload=upload(name="x.jpg", ct="image/png"), owner_subject="u"
            )
        with pytest.raises(HTTPException):
            await m.save_media(
                engine=E(rc=0),
                store=store,
                project_slug="p",
                config=MediaConfig(enabled=True, max_owner_bytes=10, allowed_mime_types=["text/plain"]),
                upload=upload(),
                owner_subject="u",
            )
        existing = {
            "id": "existing",
            "project_slug": "p",
            "storage_key": "p/a",
            "original_name": "old.txt",
            "content_type": "text/plain",
            "size": 3,
            "sha256": hashlib.sha256(b"abc").hexdigest(),
            "owner_subject": "u",
            "created_at": datetime.now(timezone.utc),
        }
        e = E(row=existing)
        out = await m.save_media(
            engine=e,
            store=store,
            project_slug="p",
            config=MediaConfig(enabled=True, deduplicate=True, deduplicate_scope="owner", allowed_mime_types=["text/plain"]),
            upload=upload(),
            owner_subject="u",
        )
        assert out["id"] == "existing" and e.inserted == 0
        e = E()
        created = await m.save_media(
            engine=e,
            store=store,
            project_slug="p",
            config=MediaConfig(enabled=True, deduplicate=False, allowed_mime_types=["text/plain"]),
            upload=upload(b"delete"),
            owner_subject="u",
        )
        e.row = created
        assert (await m.get_media_meta(e, "p", created["id"]))["storage_key"] == created["storage_key"]
        assert await m.delete_media(e, store, "p", created["id"])
        e.row = None
        with pytest.raises(HTTPException):
            await m.get_media_meta(e, "p", "missing")

    asyncio.run(run())
    with pytest.raises(RuntimeError):
        m.make_signed_media_token("p", "m", 5, secret="")
    assert not m.verify_signed_media_token("p", "m", None, secret="s" * 64)
    expired = m.make_signed_media_token("p", "m", -1, secret="s" * 64)
    assert not m.verify_signed_media_token("p", "m", expired, secret="s" * 64)


def test_mongo_registry_and_crud_paths(monkeypatch):
    import framework.mongo as m

    class AsyncCloseClient:
        async def close(self):
            return None

    class FailingCloseClient:
        def close(self):
            raise RuntimeError("close failed")

    async def disposal_edges():
        await m.MongoRegistry({"async": AsyncCloseClient()}, {}).dispose()
        with pytest.raises(RuntimeError, match="Failed to close"):
            await m.MongoRegistry({"bad": FailingCloseClient()}, {}).dispose()

    asyncio.run(disposal_edges())

    class Client:
        def __init__(self, uri, **kw):
            self.uri = uri
            self.kw = kw
            self.closed = False

        def __getitem__(self, n):
            return {"database": n}

        async def close(self):
            self.closed = True

    pm = types.ModuleType("pymongo")
    pm.AsyncMongoClient = Client
    monkeypatch.setitem(sys.modules, "pymongo", pm)
    p = ProjectConfig.model_validate(
        {
            "slug": "p",
            "name": "P",
            "security": {"jwt_enabled": False},
            "databases": {"primary": {"url": "sqlite+aiosqlite:///:memory:"}},
            "mongo_databases": {
                "main": {
                    "uri": "mongodb://x",
                    "database": "forge",
                    "max_pool_size": 25,
                    "min_pool_size": 2,
                    "server_selection_timeout_ms": 1234,
                }
            },
        }
    )

    async def reg():
        r = await m.build_mongo_registry(p)
        c = r.clients["main"]
        assert c.kw == {"maxPoolSize": 25, "minPoolSize": 2, "serverSelectionTimeoutMS": 1234}
        await r.dispose()
        assert c.closed

    asyncio.run(reg())

    class ObjectIdStub:
        @staticmethod
        def is_valid(v):
            return v == "valid"

        def __init__(self, v):
            self.value = v

    bson = types.ModuleType("bson")
    bson.ObjectId = ObjectIdStub
    monkeypatch.setitem(sys.modules, "bson", bson)
    assert isinstance(m._id_filter("valid"), ObjectIdStub) and m._id_filter("plain") == "plain"
    monkeypatch.setitem(sys.modules, "bson", None)
    assert m._id_filter("without-bson") == "without-bson"


def test_crud_runtime_paths_without_driver():
    from fastapi import HTTPException
    from sqlalchemy import Column, Integer, String, Table
    from starlette.requests import Request

    from framework.config import ResourceConfig
    from framework.crud import batch_create_rows, count_rows, create_row, delete_row, get_row, list_rows, update_row
    from framework.security import Principal

    table = Table(
        "items",
        MetaData(),
        Column("id", Integer, primary_key=True),
        Column("name", String),
        Column("tenant_id", String),
        Column("deleted_at", String),
    )
    principal = Principal(kind="api_key", subject="k", roles=set(), permissions={"*"}, tenant_id="t1")

    def res(**kw):
        d = {
            "database": "primary",
            "table": "items",
            "path": "items",
            "primary_key": "id",
            "permissions": {"list": "i.list", "read": "i.read", "create": "i.create", "update": "i.update", "delete": "i.delete"},
            "allowed_filters": ["id", "name"],
            "allowed_sort": ["id", "name"],
            "search_fields": ["name"],
            "writable_fields": ["name"],
            "tenant_field": "tenant_id",
            "soft_delete_field": "deleted_at",
            "batch_enabled": True,
            "max_batch_size": 3,
        }
        d.update(kw)
        return ResourceConfig.model_validate(d)

    def req(q=b""):
        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/items",
                "headers": [],
                "query_string": q,
                "server": ("x", 80),
                "client": ("127.0.0.1", 1),
                "scheme": "http",
            }
        )

    class Map:
        def __init__(self, rows):
            self.rows = rows

        def all(self):
            return self.rows

        def first(self):
            return self.rows[0] if self.rows else None

    class R:
        def __init__(self, rows=None, scalar=0, inserted=None, rowcount=1):
            self.rows = rows or []
            self.scalar = scalar
            self.inserted_primary_key = [] if inserted is None else [inserted]
            self.rowcount = rowcount

        def mappings(self):
            return Map(self.rows)

        def scalar_one(self):
            return self.scalar

    class Conn:
        def __init__(self, e):
            self.e = e

        async def execute(self, *a, **k):
            return self.e.results.pop(0)

    class Ctx:
        def __init__(self, e):
            self.e = e

        async def __aenter__(self):
            return Conn(self.e)

        async def __aexit__(self, *a):
            return False

    class E:
        def __init__(self, *results):
            self.results = list(results)

        def connect(self):
            return Ctx(self)

        def begin(self):
            return Ctx(self)

    async def run():
        r = res()
        assert (
            await list_rows(
                req(b"limit=2&offset=1&sort=-name&q=a"),
                E(R(rows=[{"id": 1, "name": "A", "tenant_id": "t1", "deleted_at": None}])),
                table,
                r,
                principal,
            )
        )["offset"] == 1
        with pytest.raises(HTTPException):
            await list_rows(req(b"sort=bad"), E(), table, r, principal)
        cr = res(pagination_mode="cursor", cursor_field="id", default_limit=2, max_limit=5)
        first = await list_rows(req(b"limit=2"), E(R(rows=[{"id": 1}, {"id": 2}, {"id": 3}])), table, cr, principal)
        assert first["has_more"] and first["next_cursor"]
        assert await count_rows(req(), E(R(scalar=7)), table, r, principal) == {"count": 7}
        assert (await get_row(E(R(rows=[{"id": 4, "name": "D", "tenant_id": "t1", "deleted_at": None}])), table, r, principal, "4"))[
            "id"
        ] == 4
        with pytest.raises(HTTPException):
            await get_row(E(R(rows=[])), table, r, principal, "404")
        created = await create_row(
            E(R(inserted=9), R(rows=[{"id": 9, "name": "N", "tenant_id": "t1", "deleted_at": None}])), table, r, principal, {"name": "N"}
        )
        assert created["id"] == 9
        assert await batch_create_rows(E(R(rowcount=0)), table, r, principal, [{"name": "A"}, {"name": "B"}]) == {
            "created": 2,
            "rowcount": 2,
        }
        with pytest.raises(HTTPException):
            await batch_create_rows(E(), table, res(batch_enabled=False), principal, [{"name": "A"}])
        updated = await update_row(
            E(R(rowcount=1), R(rows=[{"id": 1, "name": "C", "tenant_id": "t1", "deleted_at": None}])), table, r, principal, 1, {"name": "C"}
        )
        assert updated["name"] == "C"
        with pytest.raises(HTTPException):
            await update_row(E(R(rowcount=0)), table, r, principal, 1, {"name": "X"})
        assert await delete_row(E(R(rowcount=1)), table, r, principal, 1) == {"deleted": True}
        assert await delete_row(E(R(rowcount=1)), table, res(soft_delete_field=None), principal, 1) == {"deleted": True}

    asyncio.run(run())


def test_security_jwks_and_durable_key_edges(monkeypatch):

    import httpx
    from fastapi import HTTPException

    import framework.security as s

    s._jwks_cache.clear()
    s._jwks_locks.clear()

    class Resp:
        def __init__(self, data=None):
            self.data = data

        def json(self):
            return self.data

    class HC:
        calls = 0
        response = Resp({"keys": [{"kid": "k"}]})
        error = None
        created = []
        request_args = []

        def __init__(self, *a, **k):
            HC.created.append(k)

        async def request(self, *a, **k):
            HC.calls += 1
            HC.request_args.append((a, k))
            if HC.error:
                raise HC.error
            return HC.response

        async def close(self):
            pass

    monkeypatch.setattr(s, "ResilientHTTPClient", HC)

    async def getkeys():
        assert await s._get_jwks("https://i/j", ttl=60, timeout=1) == await s._get_jwks("https://i/j", ttl=60, timeout=1)
        assert HC.calls == 1
        assert HC.created == [{"timeout": 1, "block_private_networks": True}]
        assert HC.request_args[0][1]["max_response_bytes"] == 1024 * 1024
        assert HC.request_args[0][1]["retries"] == 1
        await s._get_jwks("https://i/j", ttl=60, timeout=1, allow_private_networks=True)
        assert HC.calls == 2
        assert HC.created[-1]["block_private_networks"] is False
        s._jwks_cache.clear()
        HC.response = Resp({"bad": []})
        with pytest.raises(HTTPException):
            await s._get_jwks("https://i/bad", ttl=60, timeout=1)
        s._jwks_cache.clear()
        HC.error = httpx.ConnectError("d", request=httpx.Request("GET", "https://i/d"))
        with pytest.raises(HTTPException):
            await s._get_jwks("https://i/d", ttl=60, timeout=1)

    asyncio.run(getkeys())
    project = ProjectConfig.model_validate(
        {
            "slug": "p",
            "name": "P",
            "databases": {"primary": {"url": "sqlite+aiosqlite:///:memory:"}},
            "security": {
                "jwt_enabled": True,
                "jwt_provider": "jwks",
                "jwt_jwks_url": "https://i/j",
                "jwt_algorithms": ["RS256"],
                "jwt_issuer": "iss",
                "jwt_audience": "aud",
            },
        }
    )
    monkeypatch.setattr(s.jwt, "get_unverified_header", lambda t: {"kid": "kid1", "alg": "RS256"})

    async def fg(*a, **k):
        return {"keys": [{"kid": "kid1", "alg": "RS256", "key_ops": ["verify"], "kty": "RSA", "n": "x", "e": "AQAB"}]}

    monkeypatch.setattr(s, "_get_jwks", fg)

    class J:
        key = "public"

    monkeypatch.setattr(s.jwt.PyJWK, "from_dict", lambda d: J())
    monkeypatch.setattr(s.jwt, "decode", lambda *a, **k: {"sub": "u", "exp": 9999999999})
    assert asyncio.run(s._decode_jwks_token("t", project))["sub"] == "u"


def test_datasource_yaml_http_mutations(tmp_path):
    import httpx
    from fastapi import HTTPException
    from starlette.requests import Request

    from framework.config import DataSourceConfig, ProjectConfig
    from framework.datasources import DataSourceManager

    p = ProjectConfig.model_validate(
        {
            "slug": "p",
            "name": "P",
            "project_dir": str(tmp_path),
            "databases": {"primary": {"url": "sqlite+aiosqlite:///:memory:"}},
            "security": {"jwt_enabled": False},
        }
    )
    m = DataSourceManager(p)
    req = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [],
            "query_string": b"q=x&q=y",
            "server": ("x", 80),
            "client": ("127.0.0.1", 1),
            "scheme": "http",
        }
    )

    async def run():
        (tmp_path / "items.yaml").write_text("- id: 1\n  name: one\n")
        src = DataSourceConfig(name="y", type="yaml_file", file="items.yaml", public=True, public_write=True, writable=True)
        plain = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/",
                "headers": [],
                "query_string": b"",
                "server": ("x", 80),
                "client": ("127.0.0.1", 1),
                "scheme": "http",
            }
        )
        assert (await m.read(src, plain))["items"][0]["name"] == "one"
        assert (await m.create(src, {"name": "two"}))["id"] == 2
        with pytest.raises(HTTPException):
            await m.create(src, "bad")
        with pytest.raises(HTTPException):
            await m.create(src, {"id": 2, "name": "dup"})
        with pytest.raises(HTTPException):
            await m.read(DataSourceConfig(name="missing", type="json_file", file="none.json", public=True), req)
        calls = []

        async def fr(method, url, **kw):
            calls.append(kw)
            return httpx.Response(200, json={"ok": True}, request=httpx.Request(method, url))

        m.private_http.request = fr
        h = DataSourceConfig(
            name="h",
            type="http",
            url="https://example.com",
            method="POST",
            public=True,
            forward_query=True,
            forward_body=True,
            retries=2,
            retry_non_idempotent=True,
            allow_private_networks=True,
        )
        assert await m.read(h, req, payload={"x": 1}) == {"ok": True}
        assert calls[0]["retry_non_idempotent"] and calls[0]["json"] == {"x": 1}
        await m.close()

    asyncio.run(run())
