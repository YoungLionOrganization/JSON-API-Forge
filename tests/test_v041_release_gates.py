from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import Boolean, Column, Integer, MetaData, String, Table
from sqlalchemy.exc import IntegrityError
from starlette.requests import Request

from framework.config import OperationConfig, ProjectConfig, ResourceConfig, SQLStatementConfig
from framework.security import Principal


def request(*, query=b"", headers=None, path="/x", method="POST"):
    r = Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": headers or [],
            "query_string": query,
            "path_params": {"id": "1"},
            "server": ("test", 80),
            "client": ("127.0.0.1", 1),
            "scheme": "http",
        }
    )
    r.state.request_id = "r1"
    r.state.validated_parameters = {"p": 1}
    return r


def project(**security):
    return ProjectConfig.model_validate(
        {
            "slug": "p",
            "name": "P",
            "databases": {"primary": {"url": "sqlite+aiosqlite:///:memory:"}},
            "security": {"jwt_enabled": False, **security},
            "roles": {"reader": {"permissions": ["notes.read"]}},
            "rate_limit": {"requests": 100, "window_seconds": 60, "burst": 20},
        }
    )


def test_security_support_schema_cache_bootstrap_delegation_and_key_crud(monkeypatch):
    import framework.security as s

    s.clear_api_key_auth_cache()

    class Mapping:
        def __init__(self, row=None, rows=None):
            self.row = row
            self.rows = rows or []

        def first(self):
            return self.row

        def all(self):
            return self.rows

    class Result:
        def __init__(self, *, first=None, maprow=None, rows=None, rowcount=1, inserted=7):
            self._first = first
            self.maprow = maprow
            self.rows = rows or []
            self.rowcount = rowcount
            self.inserted_primary_key = [inserted]

        def first(self):
            return self._first

        def mappings(self):
            return Mapping(self.maprow, self.rows)

    class Conn:
        def __init__(self, e):
            self.e = e

        async def run_sync(self, fn):
            return self.e.run_sync_result

        async def execute(self, stmt, *a, **k):
            text = str(stmt)
            self.e.statements.append(text)
            if "_forge_v4_bootstrap_state.consumed_at" in text and text.lstrip().startswith("SELECT"):
                return Result(first=self.e.bootstrap_consumed)
            if "_forge_v4_bootstrap_state.project_slug" in text and text.lstrip().startswith("SELECT"):
                return Result(first=self.e.bootstrap_exists)
            if "_forge_v2_api_keys" in text and text.lstrip().startswith("SELECT"):
                if "ORDER BY" in text:
                    return Result(rows=self.e.list_rows)
                return Result(maprow=self.e.key_row)
            if text.lstrip().startswith("UPDATE"):
                return Result(rowcount=self.e.update_count)
            if text.lstrip().startswith("INSERT"):
                return Result(inserted=7)
            return Result()

    class Ctx:
        def __init__(self, e):
            self.e = e

        async def __aenter__(self):
            return Conn(self.e)

        async def __aexit__(self, *a):
            return False

    class Engine:
        def __init__(self):
            self.run_sync_result = []
            self.bootstrap_consumed = None
            self.bootstrap_exists = None
            self.key_row = None
            self.list_rows = []
            self.update_count = 1
            self.statements = []

        def begin(self):
            return Ctx(self)

        def connect(self):
            return Ctx(self)

    e = Engine()

    async def run():
        with pytest.raises(RuntimeError):
            await s.init_security(e, mode="bad")
        await s.init_security(e, mode="create")
        e.run_sync_result = []
        await s.init_security(e, mode="validate")
        e.run_sync_result = ["missing"]
        with pytest.raises(RuntimeError):
            await s.init_security(e, mode="validate")
        e.run_sync_result = []
        assert await s.bootstrap_is_available(e, "p")
        await s.consume_bootstrap(e, "p")
        e.bootstrap_exists = ("p",)
        await s.consume_bootstrap(e, "p")
        # one-time transaction branch: insert, update, already consumed
        e.bootstrap_consumed = None
        await s._consume_bootstrap_in_transaction(await Ctx(e).__aenter__(), "p")
        e.bootstrap_consumed = (None,)
        await s._consume_bootstrap_in_transaction(await Ctx(e).__aenter__(), "p")
        e.bootstrap_consumed = (datetime.now(timezone.utc),)
        with pytest.raises(HTTPException):
            await s._consume_bootstrap_in_transaction(await Ctx(e).__aenter__(), "p")

        cfg = project(api_key_cache_ttl_seconds=10, api_key_cache_max_entries=100)
        row = {
            "id": 1,
            "name": "k",
            "prefix": "jf2_x",
            "roles": "reader",
            "permissions": "direct",
            "tenant_id": "t",
            "enabled": True,
            "rate_requests": 5,
            "rate_window_seconds": 30,
            "rate_burst": 2,
            "expires_at": None,
            "created_at": datetime.now(timezone.utc),
        }
        # cache helpers: disabled, miss, expiry, eviction, project clear
        disabled = project(api_key_cache_ttl_seconds=0)
        assert s._cached_api_key_row(disabled, "x") is None
        s._store_api_key_row(disabled, "x", row)
        assert s._cached_api_key_row(cfg, "missing") is None
        s._store_api_key_row(cfg, "a", row)
        s._store_api_key_row(cfg, "b", row)
        s._store_api_key_row(cfg, "c", row)
        assert len(s._api_key_auth_cache) <= 100
        s._api_key_auth_cache[(cfg.slug, "expired")] = (time.monotonic() - 1, row)
        assert s._cached_api_key_row(cfg, "expired") is None
        s.clear_api_key_auth_cache("p")

        e.key_row = row
        p = await s.authenticate_request(request(headers=[(b"x-api-key", b"abc")]), cfg, e)
        assert p.kind == "api_key" and "notes.read" in p.permissions
        # second call hits cache
        assert (await s.authenticate_request(request(headers=[(b"x-api-key", b"abc")]), cfg, e)).key_id == 1
        with pytest.raises(HTTPException):
            await s.authenticate_request(request(headers=[(b"x-api-key", b"")]), cfg, e)
        with pytest.raises(HTTPException):
            await s.authenticate_request(request(headers=[(b"x-api-key", b"x" * 513)]), cfg, e)
        s.clear_api_key_auth_cache()
        e.key_row = {**row, "enabled": False}
        with pytest.raises(HTTPException):
            await s.authenticate_request(request(headers=[(b"x-api-key", b"bad")]), cfg, e)
        s.clear_api_key_auth_cache()
        # SQL backends may return a naive value for a timezone-aware column.
        # Forge defines such values as timezone.utc; do not let the host timezone change
        # this regression test's meaning.
        e.key_row = {**row, "expires_at": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)}
        with pytest.raises(HTTPException):
            await s.authenticate_request(request(headers=[(b"x-api-key", b"old")]), cfg, e)

        # bootstrap query-key and one-time availability
        boot = project(bootstrap_enabled=True, bootstrap_admin_key="B" * 48, bootstrap_one_time=False, allow_query_api_key=True)
        assert (await s.authenticate_request(request(query=("api_key=" + "B" * 48).encode()), boot, e)).kind == "bootstrap"
        boot.security.bootstrap_one_time = True
        e.bootstrap_consumed = (datetime.now(timezone.utc),)
        with pytest.raises(HTTPException):
            await s.authenticate_request(request(headers=[(b"x-api-key", b"B" * 48)]), boot, e)

        e.bootstrap_consumed = None
        e.key_row = row
        made = await s.create_api_key(e, project_slug="p", name="bot", roles=["reader"], permissions=["direct"], tenant_id="t")
        assert made["id"] == 7 and made["api_key"].startswith("jf2_")
        e.list_rows = [row]
        assert (await s.list_api_keys(e, "p"))[0]["roles"] == ["reader"]
        assert (await s.get_api_key_metadata(e, "p", 1))["name"] == "k"
        e.key_row = None
        assert await s.get_api_key_metadata(e, "p", 1) is None
        e.update_count = 1
        assert await s.revoke_api_key(e, "p", 1)
        e.update_count = 0
        assert not await s.revoke_api_key(e, "p", 1)

    asyncio.run(run())

    cfg = project()
    parent = Principal(
        "api_key",
        "parent",
        {"reader"},
        {"notes.read"},
        "t",
        rate_requests=50,
        rate_window_seconds=60,
        rate_burst=10,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    s.ensure_credential_delegation(
        cfg,
        parent,
        roles=["reader"],
        permissions=[],
        tenant_id="t",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        rate_requests=40,
        rate_window_seconds=60,
        rate_burst=8,
        subject="parent",
    )
    cases = [
        dict(roles=["missing"], permissions=[], tenant_id="t"),
        dict(roles=["reader"], permissions=["admin.*"], tenant_id="t"),
        dict(roles=["reader"], permissions=[], tenant_id="other"),
        dict(roles=["reader"], permissions=[], tenant_id="t", expires_at=None),
        dict(roles=["reader"], permissions=[], tenant_id="t", expires_at=datetime.now(timezone.utc) + timedelta(hours=1)),
        dict(
            roles=["reader"], permissions=[], tenant_id="t", expires_at=datetime.now(timezone.utc) + timedelta(minutes=5), rate_requests=51
        ),
        dict(roles=["reader"], permissions=[], tenant_id="t", expires_at=datetime.now(timezone.utc) + timedelta(minutes=5), rate_burst=11),
        dict(
            roles=["reader"],
            permissions=[],
            tenant_id="t",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            rate_requests=50,
            rate_window_seconds=30,
        ),
        dict(
            roles=["reader"], permissions=[], tenant_id="t", expires_at=datetime.now(timezone.utc) + timedelta(minutes=5), subject="other"
        ),
    ]
    for kw in cases:
        with pytest.raises(HTTPException):
            s.ensure_credential_delegation(cfg, parent, **kw)
    god = Principal("api_key", "god", set(), {"admin.credentials.delegate_any"})
    # delegate_any bypasses parent scope, not the configuration schema: storing
    # an unknown role would become a latent privilege if that role were added.
    s.ensure_credential_delegation(cfg, god, roles=["reader"], permissions=["*"], tenant_id="x")


def test_security_jwt_error_and_jwks_metadata_branches(monkeypatch):
    import framework.security as s

    # local JWT missing secret / invalid / subject / project claim branches
    cfg = project(jwt_enabled=True, jwt_provider="local_hs256", jwt_secret=None, jwt_require_project_claim=True)
    monkeypatch.setattr(s.settings, "jwt_secret", None)

    async def local():
        with pytest.raises(HTTPException) as e:
            await s.authenticate_request(request(headers=[(b"authorization", b"Bearer x")]), cfg, object())
        assert e.value.status_code == 503
        cfg.security.jwt_secret = "S" * 64
        with pytest.raises(HTTPException):
            await s.authenticate_request(request(headers=[(b"authorization", b"Bearer invalid")]), cfg, object())
        tok = s.jwt.encode(
            {"sub": "u", "project": "other", "exp": datetime.now(timezone.utc) + timedelta(minutes=1)}, "S" * 64, algorithm="HS256"
        )
        with pytest.raises(HTTPException):
            await s.authenticate_request(request(headers=[(b"authorization", f"Bearer {tok}".encode())]), cfg, object())
        tok = s.jwt.encode({"project": "p", "exp": datetime.now(timezone.utc) + timedelta(minutes=1)}, "S" * 64, algorithm="HS256")
        with pytest.raises(HTTPException):
            await s.authenticate_request(request(headers=[(b"authorization", f"Bearer {tok}".encode())]), cfg, object())

    asyncio.run(local())
    jw = ProjectConfig.model_validate(
        {
            "slug": "p",
            "name": "P",
            "databases": {"primary": {"url": "sqlite+aiosqlite:///:memory:"}},
            "security": {"jwt_enabled": True, "jwt_provider": "jwks", "jwt_jwks_url": "https://x/jwks", "jwt_algorithms": ["RS256"]},
        }
    )

    async def keys(*a, **k):
        return {"keys": [{"kid": "k", "alg": "RS256", "use": "enc", "key_ops": ["verify"]}]}

    monkeypatch.setattr(s, "_get_jwks", keys)
    monkeypatch.setattr(s.jwt, "get_unverified_header", lambda t: {"kid": "k", "alg": "RS256"})
    with pytest.raises(HTTPException):
        asyncio.run(s._decode_jwks_token("x", jw))

    async def noverify(*a, **k):
        return {"keys": [{"kid": "k", "alg": "RS256", "use": "sig", "key_ops": ["sign"]}]}

    monkeypatch.setattr(s, "_get_jwks", noverify)
    with pytest.raises(HTTPException):
        asyncio.run(s._decode_jwks_token("x", jw))

    async def none(*a, **k):
        return {"keys": []}

    monkeypatch.setattr(s, "_get_jwks", none)
    with pytest.raises(HTTPException):
        asyncio.run(s._decode_jwks_token("x", jw))
    monkeypatch.setattr(s.jwt, "get_unverified_header", lambda t: (_ for _ in ()).throw(s.jwt.DecodeError("bad")))
    with pytest.raises(HTTPException):
        asyncio.run(s._decode_jwks_token("x", jw))


def test_operations_schema_safe_sql_execute_and_idempotency(monkeypatch):
    import framework.operations as o

    class Conn:
        def __init__(self, e):
            self.e = e

        async def run_sync(self, fn):
            return self.e.exists

        async def execute(self, stmt, params=None):
            return self.e.execute(stmt, params)

    class Ctx:
        def __init__(self, e):
            self.e = e

        async def __aenter__(self):
            return Conn(self.e)

        async def __aexit__(self, *a):
            return False

    class Mapping:
        def __init__(self, row=None):
            self.row = row

        def first(self):
            return self.row

    class Scalars:
        def __init__(self, vals):
            self.vals = vals

        def all(self):
            return self.vals

    class Result:
        def __init__(self, rowcount=1, row=None, vals=None):
            self.rowcount = rowcount
            self.row = row
            self.vals = vals or []
            self.inserted_primary_key = [1]

        def mappings(self):
            return Mapping(self.row)

        def scalars(self):
            return Scalars(self.vals)

        def scalar(self):
            return 1

    class E:
        def __init__(self):
            self.exists = True
            self.calls = []
            self.raise_insert = False
            self.replay = None
            self.business = 0
            self.expired = []

        def begin(self):
            return Ctx(self)

        def connect(self):
            return Ctx(self)

        def execute(self, stmt, params):
            s = str(stmt)
            self.calls.append(s)
            if s.lstrip().startswith("SELECT") and "_forge_v4_operation_idempotency" in s:
                if "request_hash" in s or "state" in s:
                    return Result(row=self.replay)
                return Result(vals=self.expired)
            if s.lstrip().startswith("INSERT") and "_forge_v4_operation_idempotency" in s and self.raise_insert:
                raise IntegrityError("insert", {}, RuntimeError("dup"))
            if "UPDATE wallet" in s:
                self.business += 1
                return Result(rowcount=1)
            return Result()

    e = E()

    async def init():
        await o.init_operation_idempotency(e, mode="create")
        e.exists = True
        await o.init_operation_idempotency(e, mode="validate")
        e.exists = False
        with pytest.raises(RuntimeError):
            await o.init_operation_idempotency(e, mode="validate")
        with pytest.raises(RuntimeError):
            await o.init_operation_idempotency(e, mode="bad")

    asyncio.run(init())
    for sql in ("", "SELECT 1; SELECT 2", "SELECT 1 --x", "DROP TABLE x"):
        with pytest.raises(RuntimeError):
            o._safe_sql(SQLStatementConfig(sql=sql, mode="execute"), False)
    with pytest.raises(RuntimeError):
        o._safe_sql(SQLStatementConfig(sql="UPDATE x SET y=1", mode="fetch_one"), False)
    o._safe_sql(SQLStatementConfig(sql="CREATE TABLE x(y int)", mode="execute"), True)

    p = Principal("api_key", "bot", set(), {"op"})
    req = request(headers=[(b"x-used", b"v")])
    body = {"amount": 5}
    op = OperationConfig.model_validate(
        {
            "name": "op",
            "permission": "op",
            "transaction": True,
            "idempotency": True,
            "idempotency_cleanup_batch_size": 10,
            "idempotency_ttl_seconds": 600,
            "statements": [
                {
                    "sql": "UPDATE wallet SET balance=balance+:amount",
                    "mode": "execute",
                    "params": {"amount": "$body.amount", "h": "$header.x-used"},
                    "result_name": "write",
                }
            ],
        }
    )
    assert o.operation_input_context(body=body, request=req, principal=p, operation=op)["headers"] == {"x-used": "v"}
    # execute_operation both context styles using non-idempotent config
    normal = OperationConfig(
        name="n",
        permission="op",
        transaction=True,
        statements=[SQLStatementConfig(sql="UPDATE wallet SET balance=balance+:amount", mode="execute", params={"amount": "$body.amount"})],
    )
    asyncio.run(o.execute_operation(e, normal, body=body, request=req, principal=p))
    readonly = OperationConfig(name="r", permission="op", transaction=False, statements=[SQLStatementConfig(sql="SELECT 1", mode="scalar")])
    asyncio.run(o.execute_operation(e, readonly, body=body, request=req, principal=p))
    # cleanup path and success
    o._idempotency_cleanup_after.clear()
    e.expired = [1, 2]
    result, replay = asyncio.run(
        o.execute_idempotent_operation(e, project_slug="p", operation=op, principal=p, raw_key="k", body=body, request=req)
    )
    assert not replay and result["write"]["rowcount"] == 1
    assert o._idempotency_cleanup_after[("p", "op")] > 0
    # transaction requirement and response max
    bad = op.model_copy(update={"transaction": False})
    with pytest.raises(RuntimeError):
        asyncio.run(o.execute_idempotent_operation(e, project_slug="p", operation=bad, principal=p, raw_key="x", body=body, request=req))
    tiny = op.model_copy(update={"idempotency_max_response_bytes": 1})
    o._idempotency_cleanup_after.clear()
    e.expired = []
    with pytest.raises(HTTPException):
        asyncio.run(
            o.execute_idempotent_operation(e, project_slug="p", operation=tiny, principal=p, raw_key="tiny", body=body, request=req)
        )
    # split phase compatibility surface
    with pytest.raises(RuntimeError):
        asyncio.run(o.claim_idempotency())
    with pytest.raises(RuntimeError):
        asyncio.run(o.complete_idempotency())
    assert asyncio.run(o.release_idempotency()) is None


def test_crud_edge_branches():
    import framework.crud as c

    table = Table(
        "t",
        MetaData(),
        Column("id", Integer, primary_key=True),
        Column("name", String, nullable=False),
        Column("optional", String, nullable=True),
        Column("active", Boolean),
        Column("tenant_id", String),
        Column("deleted_at", String),
    )
    r = ResourceConfig.model_validate(
        {
            "path": "t",
            "table": "t",
            "writable_fields": ["name", "optional", "active"],
            "allowed_filters": ["id", "name", "active"],
            "filter_operators": ["eq", "ne", "gt", "gte", "lt", "lte", "like", "ilike", "in", "isnull"],
            "allowed_sort": ["id"],
            "tenant_field": "tenant_id",
            "soft_delete_field": "deleted_at",
        }
    )
    with pytest.raises(HTTPException):
        c._write_payload("bad", r, table)
    repl = c._write_payload({"name": "x", "active": True}, r, table, mode="replace")
    assert repl["optional"] is None
    with pytest.raises(HTTPException):
        c._write_payload({"optional": "x"}, r, table, mode="replace")
    assert c._coerce_for_column(table.c.optional, None) is None
    assert c._coerce_for_column(table.c.active, "1") is True
    assert c._coerce_for_column(table.c.active, "0") is False
    with pytest.raises(HTTPException):
        c._coerce_for_column(table.c.active, "maybe")
    for op, val in [("in", "1,2"), ("isnull", "false"), ("ne", "1"), ("like", "x%"), ("ilike", "x%")]:
        assert c._filter_clause(table.c.name if "like" in op else table.c.id, op, val) is not None
    with pytest.raises(HTTPException):
        c._filter_clause(table.c.id, "bad", "1")
    # cursor encode/decode if exposed via list helpers
    assert c._parse_int(None, 3) == 3


def test_datasource_and_mongo_more_edges(tmp_path, monkeypatch):
    import framework.datasources as d
    import framework.mongo as m
    from framework.config import DataSourceConfig, MongoResourceConfig

    p = project()
    p.project_dir = str(tmp_path)
    manager = d.DataSourceManager(p)

    async def data():
        # static / csv / path escape / invalid top-level / read-only
        static = DataSourceConfig(name="s", type="static", data={"ok": 1}, public=True)
        assert await manager.read(static, request(method="GET")) == {"ok": 1}
        (tmp_path / "x.csv").write_text("id,name\n1,a\n")
        csv = DataSourceConfig(name="c", type="csv_file", file="x.csv", public=True)
        assert (await manager.read(csv, request(method="GET")))["items"][0]["name"] == "a"
        with pytest.raises(HTTPException):
            manager._file_path(DataSourceConfig(name="e", type="json_file", file="../e.json", public=True))
        (tmp_path / "bad.json").write_text('{"not":"array"}')
        bad = DataSourceConfig(name="b", type="json_file", file="bad.json", public=True)
        assert await manager.read(bad, request(method="GET")) == {"not": "array"}
        with pytest.raises(HTTPException):
            await manager.create(static, {})
        with pytest.raises(HTTPException):
            await manager.update(static, "1", {})
        with pytest.raises(HTTPException):
            await manager.delete(static, "1")
        await manager.close()

    asyncio.run(data())

    resource = MongoResourceConfig.model_validate(
        {
            "database": "m",
            "collection": "items",
            "path": "items",
            "tenant_field": "tenant_id",
            "owner_field": "owner",
            "owner_actions": ["read", "update", "delete"],
            "owner_bypass_permission": "items.bypass",
            "soft_delete_field": "deleted_at",
            "writable_fields": ["name"],
            "allowed_filters": ["name"],
            "filter_operators": ["eq", "ne", "in"],
            "allowed_sort": ["name"],
        }
    )
    principal = Principal("api_key", "u", set(), set(), "t")
    with pytest.raises(HTTPException):
        m._write_payload("bad", resource, None)
    with pytest.raises(HTTPException):
        m._write_payload({"owner": "x"}, resource, None)
    with pytest.raises(HTTPException):
        m._base_filter(resource, Principal("anonymous", "a", set(), set(), "t"), action="read")
    bypass = Principal("api_key", "admin", set(), {"items.bypass"}, "t")
    assert "owner" not in m._base_filter(resource, bypass, action="read")
    with pytest.raises(HTTPException):
        m._positive_int(request(query=b"limit=x"), "limit", 1, minimum=1)
    with pytest.raises(HTTPException):
        m._positive_int(request(query=b"limit=0"), "limit", 1, minimum=1)
    assert m._query_filter(request(query=b"name__ne=x&name__in=a,b"), resource, principal)["name"] in (
        {"$in": ["a", "b"]},
        {"$ne": "x", "$in": ["a", "b"]},
    )

    class Cursor:
        def __init__(self, rows):
            self.rows = list(rows)

        def skip(self, n):
            self.rows = self.rows[n:]
            return self

        def limit(self, n):
            self.rows = self.rows[:n]
            return self

        def sort(self, *a):
            return self

        async def to_list(self, length):
            return self.rows[:length]

    class Col:
        def __init__(self):
            self.rows = {"1": {"_id": "1", "name": "a", "tenant_id": "t", "owner": "u", "deleted_at": None}}

        def find(self, f):
            return Cursor(list(self.rows.values()))

        async def count_documents(self, f):
            return 1

        async def find_one(self, f):
            row = self.rows.get(str(f.get("_id", "1")))
            return dict(row) if row else None

        async def insert_one(self, data):
            self.rows["2"] = {"_id": "2", **data}
            return SimpleNamespace(inserted_id="2")

        async def update_one(self, f, u):
            row = await self.find_one(f)
            if not row:
                return SimpleNamespace(matched_count=0)
            self.rows[str(f["_id"])].update(u["$set"])
            return SimpleNamespace(matched_count=1)

        async def replace_one(self, f, r):
            if not await self.find_one(f):
                return SimpleNamespace(matched_count=0)
            self.rows[str(f["_id"])] = dict(r)
            return SimpleNamespace(matched_count=1)

        async def delete_one(self, f):
            return SimpleNamespace(deleted_count=1)

    db = {"items": Col()}

    async def mongo():
        assert (await m.list_documents(request(query=b"sort=-name"), db, resource, principal))["items"]
        assert (await m.count_documents(request(), db, resource, principal))["count"] == 1
        assert (await m.get_document(db, resource, principal, "1"))["name"] == "a"
        created = await m.create_document(db, resource, principal, {"name": "b"})
        assert created["tenant_id"] == "t" and created["owner"] == "u"
        assert (await m.update_document(db, resource, principal, "1", {"name": "c"}, replace=True))["name"] == "c"
        with pytest.raises(HTTPException):
            await m.update_document(db, resource, principal, "1", {})
        assert await m.delete_document(db, resource, principal, "1") == {"deleted": True}
        no_soft = resource.model_copy(update={"soft_delete_field": None})
        assert await m.delete_document(db, no_soft, principal, "1") == {"deleted": True}

    asyncio.run(mongo())


def test_audit_permanent_shutdown_and_runtime_cleanup(monkeypatch):
    import framework.runtime as rt
    from framework.audit import AuditWriter

    class BadConn:
        async def execute(self, *a, **k):
            raise RuntimeError("db")

    class BadCtx:
        async def __aenter__(self):
            return BadConn()

        async def __aexit__(self, *a):
            return False

    class BadE:
        def begin(self):
            return BadCtx()

    async def audit():
        w = AuditWriter(BadE(), max_queue=4, batch_size=1, flush_interval=0.01, write_retries=0, retry_backoff_seconds=0)
        await w.start()
        w.submit(
            project_slug="p",
            request_id="r",
            principal_kind="x",
            principal_subject="x",
            method="GET",
            path="/",
            status_code=500,
            duration_ms=1,
        )
        await asyncio.sleep(0.03)
        await w.close()
        assert w.write_failures >= 1 and w.dropped >= 1
        # start twice branch
        e = SimpleNamespace(begin=lambda: BadCtx())
        w = AuditWriter(e)
        await w.start()
        task = w._task
        await w.start()
        assert w._task is task
        w._task.cancel()
        try:
            await w._task
        except asyncio.CancelledError:
            pass

    asyncio.run(audit())

    class Service:
        def __init__(self, fail=False):
            self.fail = fail
            self.closed = False

        async def close(self):
            self.closed = True

        async def dispose(self):
            self.closed = True

    p = project()
    forge = rt.ForgeConfig(projects=[p])
    manager = rt.RuntimeManager(forge)
    runtime = manager.runtimes["p"]
    runtime.event_hub = Service()
    runtime.data_sources = Service()
    runtime.cache = Service()
    runtime.limiter = Service()
    runtime.mongo_registry = Service()
    runtime.registry = Service()
    runtime.registry.dispose = runtime.registry.dispose

    async def cleanup():
        await manager._close_runtime(runtime, suppress=False)
        assert runtime.gate is None and runtime.media_store is None

    asyncio.run(cleanup())
    runtime.registry = Service(True)

    async def bad_dispose():
        raise RuntimeError("close")

    runtime.registry.dispose = bad_dispose
    with pytest.raises(RuntimeError):
        asyncio.run(manager._close_runtime(runtime, suppress=False))


def test_audit_flush_timeout_backoff_and_shutdown_timeout(monkeypatch):
    from framework.audit import AuditWriter

    class Conn:
        def __init__(self, e):
            self.e = e

        async def execute(self, *a, **k):
            self.e.calls += 1
            if self.e.calls == 1 and self.e.fail_once:
                raise RuntimeError("once")

    class Ctx:
        def __init__(self, e):
            self.e = e

        async def __aenter__(self):
            return Conn(self.e)

        async def __aexit__(self, *a):
            return False

    class E:
        def __init__(self, fail_once=False):
            self.fail_once = fail_once
            self.calls = 0

        def begin(self):
            return Ctx(self)

    async def run():
        # retry with actual backoff branch
        e = E(True)
        w = AuditWriter(e, write_retries=1, retry_backoff_seconds=0.001)
        assert await w._write_batch_with_retry([{"x": 1}])
        assert e.calls == 2
        # batching loop reaches wait_for timeout naturally
        e = E()
        w = AuditWriter(e, batch_size=3, flush_interval=0.01)
        await w.start()
        w.submit(
            project_slug="p",
            request_id="r",
            principal_kind="x",
            principal_subject="x",
            method="GET",
            path="/",
            status_code=200,
            duration_ms=1,
        )
        await asyncio.sleep(0.03)
        await w.close()
        assert e.calls >= 1
        # no worker: queue.join timeout accounts the stranded record
        w = AuditWriter(E(), shutdown_timeout_seconds=0.1)
        w.submit(
            project_slug="p",
            request_id="stranded",
            principal_kind="x",
            principal_subject="x",
            method="GET",
            path="/",
            status_code=200,
            duration_ms=1,
        )
        await w.close()
        assert w.dropped == 1

    asyncio.run(run())


def test_datasource_validation_query_write_and_upstream_errors(tmp_path, monkeypatch):
    import httpx

    import framework.datasources as d
    from framework.config import DataSourceConfig
    from framework.services.http_client import ResponseTooLarge

    p = project()
    p.project_dir = str(tmp_path)
    m = d.DataSourceManager(p)

    async def run():
        for src in [
            DataSourceConfig.model_construct(
                name="invalid", type="http", url="ftp://x", public=True, allow_insecure_http=False, allow_private_networks=False
            ),
            DataSourceConfig.model_construct(
                name="creds",
                type="http",
                url="https://u:p@example.com",
                public=True,
                allow_insecure_http=False,
                allow_private_networks=False,
            ),
            DataSourceConfig.model_construct(
                name="plain", type="http", url="http://example.com", public=True, allow_insecure_http=False, allow_private_networks=False
            ),
            DataSourceConfig.model_construct(
                name="private", type="http", url="https://127.0.0.1", public=True, allow_insecure_http=False, allow_private_networks=False
            ),
        ]:
            with pytest.raises(HTTPException):
                await m._validate_http_target(src)
        assert m._blocked_address("127.0.0.1") and not m._blocked_address("8.8.8.8")
        # collection validation branches
        src = DataSourceConfig(
            name="q", type="static", data=[{"name": "b"}, {"name": "a"}], public=True, allowed_filters=["name"], allowed_sort=["name"]
        )
        with pytest.raises(HTTPException):
            m._query_collection(src.data, src, request(query=b"other=x"))
        with pytest.raises(HTTPException):
            m._query_collection(src.data, src, request(query=b"sort=other"))
        with pytest.raises(HTTPException):
            m._query_collection(src.data, src, request(query=b"limit=x"))
        with pytest.raises(HTTPException):
            m._query_collection(src.data, src, request(query=b"limit=0"))
        assert m._query_collection(src.data, src, request(query=b"name=a&sort=-name&limit=1"))["items"][0]["name"] == "a"
        # JSON/YAML write, replace/delete and error paths
        (tmp_path / "items.json").write_text('[{"id":1,"name":"one"}]')
        writable = DataSourceConfig(
            name="w", type="json_file", file="items.json", public=True, public_write=True, writable=True, max_response_bytes=10000
        )
        victim = tmp_path / "victim.txt"
        victim.write_text("must remain unchanged")
        predictable_tmp = tmp_path / "race.json.tmp"
        predictable_tmp.symlink_to(victim)
        race = DataSourceConfig(name="race", type="json_file", file="race.json", public=True, public_write=True, writable=True)
        m._write_file_sync(race, [{"id": 1}])
        assert victim.read_text() == "must remain unchanged"
        assert predictable_tmp.is_symlink()
        assert (await m.update(writable, "1", {"name": "two"}))["name"] == "two"
        rep = await m.update(writable, "1", {"name": "three"}, replace=True)
        assert rep == {"name": "three", "id": 1}
        with pytest.raises(HTTPException):
            await m.update(writable, "1", {"id": 2})
        with pytest.raises(HTTPException):
            await m.update(writable, "404", {"name": "x"})
        assert await m.delete(writable, "1") == {"deleted": True}
        with pytest.raises(HTTPException):
            await m.delete(writable, "1")
        with pytest.raises(RuntimeError):
            m._mutate_file_sync(writable, "unknown")
        yaml_src = DataSourceConfig(name="y", type="yaml_file", file="y.yaml", public=True, public_write=True, writable=True)
        m._write_file_sync(yaml_src, [{"id": 1}])
        assert "id" in (tmp_path / "y.yaml").read_text()
        csv_src = DataSourceConfig(name="csv", type="csv_file", file="x.csv", public=True)
        with pytest.raises(HTTPException):
            m._write_file_sync(csv_src, [])
        tiny = DataSourceConfig.model_construct(
            name="tiny", type="json_file", file="tiny.json", public=True, public_write=True, writable=True, max_response_bytes=1
        )
        with pytest.raises(HTTPException):
            m._write_file_sync(tiny, [1])
        # upstream response error branches without network
        h = DataSourceConfig(name="h", type="http", url="https://example.com", public=True, allow_private_networks=True)

        async def too_large(*a, **k):
            raise ResponseTooLarge("large")

        m.private_http.request = too_large
        with pytest.raises(HTTPException):
            await m.read(h, request(method="GET"))

        async def invalid_json(method, url, **k):
            return httpx.Response(
                200, content=b"not-json", headers={"content-type": "application/json"}, request=httpx.Request(method, url)
            )

        m.private_http.request = invalid_json
        with pytest.raises(HTTPException):
            await m.read(h, request(method="GET"))

        async def text(method, url, **k):
            return httpx.Response(502, text="bad", headers={"content-type": "text/plain"}, request=httpx.Request(method, url))

        m.private_http.request = text
        assert await m.read(h, request(method="GET")) == {"status_code": 502, "text": "bad"}
        await m.close()

    asyncio.run(run())
