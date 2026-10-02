from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from framework.config import (
    CustomEndpointConfig,
    DataSourceConfig,
    EventChannelConfig,
    OperationConfig,
    ProjectConfig,
    RequestParameterSpec,
    SQLStatementConfig,
)
from framework.doctor import is_weak_secret, normalize_route, project_diagnostics
from framework.events import EventHub, sse_encode
from framework.operations import _safe_sql, idempotency_digest, request_fingerprint
from framework.protection import ConcurrencyGate, RequestBodyLimitMiddleware, client_ip, ip_allowed
from framework.rate_limit import MemoryRateLimiter
from framework.security import Principal
from framework.validation import openapi_parameters, validate_json_schema, validate_request_parameters


def _request(*, path="/x/7", method="POST", query=b"", headers=None, path_params=None):
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": headers or [],
            "query_string": query,
            "path_params": path_params or {},
            "server": ("test", 80),
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
        }
    )


def test_secure_by_default_configs_require_explicit_public_or_permission():
    with pytest.raises(ValidationError):
        OperationConfig(name="x", statements=[SQLStatementConfig(sql="SELECT 1", mode="scalar")])
    assert OperationConfig(name="x", public=True, transaction=False, statements=[SQLStatementConfig(sql="SELECT 1", mode="scalar")]).public
    with pytest.raises(ValidationError):
        CustomEndpointConfig(path="x", handler="a:b")
    assert CustomEndpointConfig(path="x", handler="a:b", public=True).public
    with pytest.raises(ValidationError):
        DataSourceConfig(name="x", type="static", data={})
    assert DataSourceConfig(name="x", type="static", data={}, public=True).public
    with pytest.raises(ValidationError):
        EventChannelConfig(name="x")
    assert EventChannelConfig(name="x", public_publish=True, public_subscribe=True).path == "events/x"


def test_strict_config_and_idempotency_constraints():
    with pytest.raises(ValidationError):
        DataSourceConfig(name="x", type="static", data={}, public=True, typo_field=True)
    stmt = SQLStatementConfig(sql="UPDATE x SET y=1", mode="execute")
    with pytest.raises(ValidationError):
        OperationConfig(name="x", permission="x", idempotency=True, transaction=False, statements=[stmt])
    with pytest.raises(ValidationError):
        OperationConfig(name="x", permission="x", idempotency=True, cache={"enabled": True}, statements=[stmt])


def test_idempotency_digest_fingerprint_and_sql_guardrails():
    p = Principal(kind="api_key", subject="plugin", roles=set(), permissions=set())
    assert idempotency_digest("p", "op", p, "same") != idempotency_digest(
        "p", "op", Principal(kind="api_key", subject="other", roles=set(), permissions=set()), "same"
    )
    op = OperationConfig(name="fp", public=True, transaction=False, statements=[SQLStatementConfig(sql="SELECT 1", mode="scalar")])
    req = _request(path="/op/1", path_params={"id": "1"})
    req.state.validated_parameters = {"page": 2}
    assert request_fingerprint(body={"amount": 10}, request=req, principal=p, operation=op) != request_fingerprint(
        body={"amount": 11}, request=req, principal=p, operation=op
    )
    with pytest.raises(RuntimeError):
        _safe_sql(SQLStatementConfig(sql="SELECT 1; SELECT 2", mode="fetch_all"), False)
    with pytest.raises(RuntimeError):
        _safe_sql(SQLStatementConfig(sql="DROP TABLE users", mode="execute"), False)
    _safe_sql(SQLStatementConfig(sql="SELECT 1", mode="scalar"), False)


def test_doctor_detects_route_collision_and_weak_secrets():
    project = ProjectConfig.model_validate(
        {
            "slug": "p",
            "name": "P",
            "databases": {"primary": {"url": "sqlite+aiosqlite:///x.db"}},
            "security": {"jwt_enabled": False},
            "operations": [
                {
                    "name": "a",
                    "path": "same/{id}",
                    "method": "GET",
                    "public": True,
                    "transaction": False,
                    "statements": [{"sql": "SELECT 1", "mode": "scalar"}],
                }
            ],
            "custom_endpoints": [{"path": "same/{other}", "method": "GET", "public": True, "handler": "x:y"}],
        }
    )
    assert any(d.code == "route-collision" for d in project_diagnostics(project))
    assert normalize_route("/a/{id}//b") == "/a/{}/b"
    assert is_weak_secret("change-me-long-but-still-default-xxxxxxxx") and not is_weak_secret("A9_qK7vT2mP8wR4yN6cH3sF5zL1bD9gJ0uX7eC2rV8")


def test_memory_rate_limiter_bounds_high_cardinality_state():
    async def run():
        limiter = MemoryRateLimiter(max_buckets=100, idle_ttl_seconds=10, cleanup_interval_seconds=1)
        for i in range(160):
            await limiter.check(f"identity-{i}", limit=100, window_seconds=60)
        assert len(limiter._buckets) <= 100
        await limiter.close()
        assert not limiter._buckets

    asyncio.run(run())


def test_concurrency_gate_reject_and_wait_modes():
    async def run():
        reject = ConcurrencyGate(1, 0.01, True)
        await reject.__aenter__()
        with pytest.raises(HTTPException) as exc:
            await reject.__aenter__()
        assert exc.value.status_code == 503
        await reject.__aexit__(None, None, None)
        wait = ConcurrencyGate(1, 0.2, False)
        await wait.__aenter__()
        entered = False

        async def contender():
            nonlocal entered
            async with wait:
                entered = True

        task = asyncio.create_task(contender())
        await asyncio.sleep(0.02)
        assert not entered
        await wait.__aexit__(None, None, None)
        await task
        assert entered

    asyncio.run(run())


def test_streaming_body_limit_middleware_counts_chunks():
    async def run():
        called = False

        async def inner(scope, receive, send):
            nonlocal called
            called = True
            while True:
                m = await receive()
                if not m.get("more_body"):
                    break
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b"", "more_body": False})

        mw = RequestBodyLimitMiddleware(inner, lambda _: 5)
        chunks = iter(
            [{"type": "http.request", "body": b"abc", "more_body": True}, {"type": "http.request", "body": b"def", "more_body": False}]
        )
        sent = []
        await mw(
            {"type": "http", "path": "/x", "headers": []},
            lambda: asyncio.sleep(0, result=next(chunks)),
            lambda m: asyncio.sleep(0, result=sent.append(m)),
        )
        assert called and sent[0]["status"] == 413

    asyncio.run(run())


def test_event_hub_bounded_queue_and_sse_encoding():
    async def run():
        hub = EventHub()
        sub = hub.subscribe("x", 1)
        waiter = asyncio.create_task(anext(sub))
        await asyncio.sleep(0)
        assert await hub.publish("x", {"n": 1}) == 1
        assert await waiter == {"n": 1}
        await sub.aclose()
        await hub.close()

    asyncio.run(run())
    assert sse_encode({"x": 1}).startswith(b"data: ")


def test_validation_schema_and_parameters():
    with pytest.raises(HTTPException):
        validate_json_schema({"x": 0}, {"type": "object", "properties": {"x": {"minimum": 1}}})
    request = _request(query=b"page=3&enabled=true")
    assert validate_request_parameters(
        request, [RequestParameterSpec(name="page", type="integer", minimum=1), RequestParameterSpec(name="enabled", type="boolean")]
    ) == {"page": 3, "enabled": True}
    with pytest.raises(HTTPException):
        validate_request_parameters(_request(), [RequestParameterSpec(name="missing", required=True)])
    with pytest.raises(HTTPException):
        validate_request_parameters(_request(query=b"b=maybe"), [RequestParameterSpec(name="b", type="boolean")])
    params = openapi_parameters(
        [RequestParameterSpec(name="page", type="integer", required=True, minimum=1, maximum=10, enum=[1, 2, 3], default=1)]
    )
    assert params[0]["schema"] == {"type": "integer", "enum": [1, 2, 3], "minimum": 1, "maximum": 10, "default": 1}


def test_ip_protection_header_paths():
    req = _request()
    assert client_ip(req) == "127.0.0.1" and ip_allowed(req, ["127.0.0.0/8"], []) and not ip_allowed(req, [], ["127.0.0.1"])


def test_idempotency_retention_janitor_is_throttled(monkeypatch):
    import framework.operations as operations

    op = OperationConfig(
        name="retained",
        permission="op.run",
        idempotency=True,
        transaction=True,
        idempotency_ttl_seconds=600,
        statements=[SQLStatementConfig(sql="SELECT 1", mode="scalar")],
    )
    clock = {"value": 1000.0}
    monkeypatch.setattr(operations, "monotonic", lambda: clock["value"])
    operations._idempotency_cleanup_after.clear()
    assert operations._idempotency_cleanup_due("app", op)
    assert not operations._idempotency_cleanup_due("app", op)
    clock["value"] += 60
    assert operations._idempotency_cleanup_due("app", op)
    operations._idempotency_cleanup_after.clear()


def test_idempotency_ledger_has_retention_index():
    from framework.operations import operation_idempotency_table

    assert "ix_forge_v4_idempotency_retention" in {i.name for i in operation_idempotency_table.indexes}
