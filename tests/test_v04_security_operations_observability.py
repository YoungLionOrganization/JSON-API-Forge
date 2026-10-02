from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import httpx
import jwt
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from framework.audit import AuditWriter
from framework.config import OperationConfig, ProjectConfig, ResponseSpec, SQLStatementConfig
from framework.operations import _execute_on_connection, resolve_value
from framework.responses import render_response
from framework.security import (
    Principal,
    _claim,
    _claim_set,
    _expand_role_permissions,
    authenticate_request,
    has_permission,
    hash_key,
    issue_jwt,
    make_api_key,
    permission_matches,
)
from framework.services.http_client import ResilientHTTPClient
from framework.settings import settings


def _request(*, headers=None, query=b"", path_params=None):
    r = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/op/7",
            "headers": headers or [],
            "query_string": query,
            "path_params": path_params or {"id": "7"},
            "server": ("test", 80),
            "client": ("127.0.0.1", 1),
            "scheme": "http",
        }
    )
    r.state.request_id = "req-1"
    r.state.validated_parameters = {"page": 3}
    return r


def _project(**security):
    return ProjectConfig.model_validate(
        {
            "slug": "p",
            "name": "P",
            "databases": {"primary": {"url": "sqlite+aiosqlite:///x.db"}},
            "security": {"jwt_enabled": True, "jwt_provider": "local_hs256", **security},
            "roles": {
                "reader": {"permissions": ["notes.read"]},
                "editor": {"permissions": ["notes.write"], "inherits": ["reader"]},
                "loop-a": {"permissions": ["a"], "inherits": ["loop-b"]},
                "loop-b": {"permissions": ["b"], "inherits": ["loop-a"]},
            },
        }
    )


def test_security_claims_permissions_roles_local_jwt(monkeypatch):
    assert _claim({"a": {"b": 2}}, "a.b") == 2
    assert _claim({"a": {}}, "a.x", "d") == "d"
    assert _claim_set(None) == set()
    assert _claim_set("x") == {"x"}
    assert _claim_set(["x", None, 2]) == {"x", "2"}
    assert _claim_set(9) == {"9"}
    assert permission_matches("*", "x") and permission_matches("notes.*", "notes.read") and not permission_matches("notes.*", "users.read")
    p = Principal(kind="api_key", subject="x", roles=set(), permissions={"notes.*"})
    assert has_permission(p, "notes.write") and has_permission(p, None)
    project = _project()
    assert _expand_role_permissions(project, {"editor"}) == {"notes.read", "notes.write"}
    assert _expand_role_permissions(project, {"loop-a"}) == {"a", "b"}
    monkeypatch.setattr(settings, "jwt_secret", "S" * 64)
    token = issue_jwt("user-1", "p", ["editor"], ["direct"], 5, "tenant-1")

    async def run():
        principal = await authenticate_request(_request(headers=[(b"authorization", f"Bearer {token}".encode())]), project, object())
        assert (
            principal.kind == "jwt" and principal.subject == "user-1" and {"direct", "notes.read", "notes.write"} <= principal.permissions
        )
        bad = jwt.encode(
            {"sub": "u", "project": "other", "exp": datetime.now(timezone.utc).timestamp() + 60}, settings.jwt_secret, algorithm="HS256"
        )
        with pytest.raises(HTTPException):
            await authenticate_request(_request(headers=[(b"authorization", f"Bearer {bad}".encode())]), project, object())
        assert (await authenticate_request(_request(), project, object())).kind == "anonymous"

    asyncio.run(run())
    assert hash_key("a") != hash_key("b") and make_api_key().startswith("jf2_")


def test_project_scoped_local_jwt_secret_overrides_global(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", "G" * 64)
    project = _project(jwt_secret="P" * 64)
    good = issue_jwt("user-project", "p", [], ["notes.read"], 5, secret="P" * 64)
    wrong = issue_jwt("wrong", "p", [], [], 5)

    async def run():
        assert (
            await authenticate_request(_request(headers=[(b"authorization", f"Bearer {good}".encode())]), project, object())
        ).subject == "user-project"
        with pytest.raises(HTTPException):
            await authenticate_request(_request(headers=[(b"authorization", f"Bearer {wrong}".encode())]), project, object())

    asyncio.run(run())


def test_operation_resolution_modes_and_guards():
    principal = Principal(kind="api_key", subject="plugin", roles=set(), permissions=set(), tenant_id="t")
    request = _request(query=b"q=hello", headers=[(b"x-test", b"hdr")])
    body = {"user": {"id": 9}}
    expected = {
        "$principal.subject": "plugin",
        "$principal.tenant_id": "t",
        "$principal.kind": "api_key",
        "$request.id": "req-1",
        "$body.user.id": 9,
        "$param.page": 3,
        "$query.q": "hello",
        "$path.id": "7",
        "$header.x-test": "hdr",
    }
    for expr, value in expected.items():
        assert resolve_value(expr, body=body, request=request, principal=principal) == value
    assert resolve_value("$$body.x", body=body, request=request, principal=principal) == "$body.x"
    for expr in ("$body.missing", "$query.nope", "$unknown.x"):
        with pytest.raises(HTTPException):
            resolve_value(expr, body=body, request=request, principal=principal)

    class M:
        def __init__(self, rows):
            self.rows = rows

        def first(self):
            return self.rows[0] if self.rows else None

        def fetchmany(self, n):
            return self.rows[:n]

    class R:
        def __init__(self, rc=1, rows=None, scalar=None):
            self.rowcount = rc
            self.rows = rows or []
            self.scalar_value = scalar

        def mappings(self):
            return M(self.rows)

        def scalar(self):
            return self.scalar_value

    class C:
        def __init__(self, results):
            self.results = list(results)

        async def execute(self, *a, **k):
            return self.results.pop(0)

    op = OperationConfig.model_validate(
        {
            "name": "all",
            "permission": "op",
            "transaction": True,
            "statements": [
                {"sql": "UPDATE t SET x=:x", "mode": "execute", "params": {"x": "$body.user.id"}, "result_name": "exec"},
                {"sql": "SELECT x FROM t", "mode": "fetch_one", "result_name": "one"},
                {"sql": "SELECT x FROM t", "mode": "fetch_all", "max_rows": 2, "result_name": "many"},
                {"sql": "SELECT 7", "mode": "scalar"},
            ],
        }
    )
    out = asyncio.run(
        _execute_on_connection(
            C([R(), R(rows=[{"x": datetime(2026, 1, 1, tzinfo=timezone.utc)}]), R(rows=[{"x": 1}, {"x": 2}]), R(scalar=7)]),
            op,
            body=body,
            request=request,
            principal=principal,
        )
    )
    assert (
        out["exec"] == {"rowcount": 1}
        and out["one"]["x"].startswith("2026-01-01")
        and out["many"] == [{"x": 1}, {"x": 2}]
        and out["results"] == [7]
    )
    low = OperationConfig(
        name="low",
        permission="op",
        transaction=True,
        statements=[SQLStatementConfig(sql="UPDATE x SET y=1", mode="execute", require_rowcount_min=1)],
    )
    with pytest.raises(HTTPException):
        asyncio.run(_execute_on_connection(C([R(rc=0)]), low, body={}, request=request, principal=principal))
    high = OperationConfig(
        name="high",
        permission="op",
        transaction=True,
        statements=[SQLStatementConfig(sql="UPDATE x SET y=1", mode="execute", require_rowcount_max=1)],
    )
    with pytest.raises(HTTPException):
        asyncio.run(_execute_on_connection(C([R(rc=2)]), high, body={}, request=request, principal=principal))
    many = OperationConfig(
        name="many",
        permission="op",
        transaction=False,
        statements=[SQLStatementConfig(sql="SELECT x FROM t", mode="fetch_all", max_rows=1)],
    )
    with pytest.raises(HTTPException):
        asyncio.run(_execute_on_connection(C([R(rows=[{"x": 1}, {"x": 2}])]), many, body={}, request=request, principal=principal))


def test_response_rendering_all_kinds(tmp_path):
    for spec, value, code in [
        (ResponseSpec(kind="json"), {"x": 1}, 200),
        (ResponseSpec(kind="text", media_type="text/plain"), "hello", 200),
        (ResponseSpec(kind="html"), "<b>x</b>", 200),
        (ResponseSpec(kind="redirect"), {"url": "https://example.com"}, 307),
        (ResponseSpec(kind="empty"), None, 204),
    ]:
        assert render_response(spec, value).status_code == code
    assert render_response(ResponseSpec(kind="stream"), iter([b"a"])).status_code == 200
    p = tmp_path / "x.txt"
    p.write_text("x")
    assert Path(render_response(ResponseSpec(kind="file", filename="download.txt"), {"path": str(p)}).path) == p
    # v0.4.1 JSONResponse compatibility: datetime must still serialize.
    response = render_response(ResponseSpec(kind="json"), {"when": datetime(2026, 1, 1, tzinfo=timezone.utc)})
    assert b"2026-01-01" in response.body


def test_audit_writer_overflow_batch_and_retry(caplog):
    class Conn:
        def __init__(self, e):
            self.e = e

        async def execute(self, stmt, batch):
            self.e.attempts += 1
            if self.e.fail_once and self.e.attempts == 1:
                raise RuntimeError("temporary")
            self.e.saved.extend(batch)

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
            self.attempts = 0
            self.saved = []

        def begin(self):
            return Ctx(self)

    async def run():
        e = E()
        w = AuditWriter(e, max_queue=1, batch_size=1, flush_interval=0.01)
        w.submit(
            project_slug="p",
            request_id="1",
            principal_kind="x",
            principal_subject="x",
            method="GET",
            path="/",
            status_code=200,
            duration_ms=1,
        )
        w.submit(
            project_slug="p",
            request_id="2",
            principal_kind="x",
            principal_subject="x",
            method="GET",
            path="/",
            status_code=200,
            duration_ms=1,
        )
        assert w.dropped == 1
        await w.start()
        await asyncio.sleep(0.03)
        await w.close()
        assert len(e.saved) == 1
        e = E(True)
        w = AuditWriter(e, max_queue=10, batch_size=1, flush_interval=0.01, write_retries=1, retry_backoff_seconds=0)
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
        assert e.attempts == 2 and len(e.saved) == 1 and w.write_failures == 1

    asyncio.run(run())
    assert "dropping audit event" in caplog.text


def test_http_retry_circuit_and_permanent_status(monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    async def run():
        c = ResilientHTTPClient(failure_threshold=2, reset_seconds=60)
        calls = 0

        async def send(method, url, **kwargs):
            nonlocal calls
            calls += 1
            req = httpx.Request(method, url)
            if calls < 2:
                raise httpx.ConnectError("boom", request=req)
            return httpx.Response(200, json={"ok": True}, request=req)

        c._send_once = send
        assert (await c.request("GET", "https://example.com", retries=1)).json() == {"ok": True}
        await c.close()
        post = ResilientHTTPClient()
        pcalls = 0

        async def fail_post(method, url, **kwargs):
            nonlocal pcalls
            pcalls += 1
            raise httpx.ConnectError("boom", request=httpx.Request(method, url))

        post._send_once = fail_post
        with pytest.raises(httpx.ConnectError):
            await post.request("POST", "https://example.com", retries=5)
        assert pcalls == 1
        await post.close()
        nf = ResilientHTTPClient(failure_threshold=1)
        ncalls = 0

        async def ret404(method, url, **kwargs):
            nonlocal ncalls
            ncalls += 1
            return httpx.Response(404, request=httpx.Request(method, url))

        nf._send_once = ret404
        with pytest.raises(httpx.HTTPStatusError):
            await nf.request("GET", "https://example.com/missing", retries=3)
        assert ncalls == 1 and nf.states["example.com"].opened_at is None
        await nf.close()

    asyncio.run(run())


def test_observability_metric_paths(monkeypatch):
    import framework.observability as obs

    class Metric:
        def __init__(self):
            self.calls = []

        def labels(self, **kw):
            self.calls.append(("labels", kw))
            return self

        def inc(self, v=1):
            self.calls.append(("inc", v))

        def observe(self, v):
            self.calls.append(("observe", v))

        def set(self, v):
            self.calls.append(("set", v))

    req = Metric()
    lat = Metric()
    drop = Metric()
    fail = Metric()
    q = Metric()
    monkeypatch.setattr(obs, "_REQUESTS", req)
    monkeypatch.setattr(obs, "_LATENCY", lat)
    monkeypatch.setattr(obs, "_AUDIT_DROPPED", drop)
    monkeypatch.setattr(obs, "_AUDIT_WRITE_FAILURES", fail)
    monkeypatch.setattr(obs, "_AUDIT_QUEUE", q)
    monkeypatch.setattr(obs, "generate_latest", lambda: b"metric 1\n")
    monkeypatch.setattr(obs, "CONTENT_TYPE_LATEST", "text/plain")
    obs.observe("app", "GET", 200, 0.25)
    obs.observe_audit_drop(2)
    obs.observe_audit_write_failure(3)
    obs.observe_audit_queue(4)
    assert obs.metrics_payload() == (b"metric 1\n", "text/plain")
    assert ("observe", 0.25) in lat.calls and ("inc", 2) in drop.calls and ("set", 4) in q.calls
    monkeypatch.setattr(obs, "_REQUESTS", None)
    monkeypatch.setattr(obs, "_AUDIT_DROPPED", None)
    monkeypatch.setattr(obs, "_AUDIT_WRITE_FAILURES", None)
    monkeypatch.setattr(obs, "_AUDIT_QUEUE", None)
    monkeypatch.setattr(obs, "generate_latest", None)
    obs.observe("app", "GET", 500, 1)
    obs.observe_audit_drop()
    obs.observe_audit_write_failure()
    obs.observe_audit_queue(0)
    assert obs.metrics_payload() == (b"prometheus-client is not installed\n", "text/plain")
