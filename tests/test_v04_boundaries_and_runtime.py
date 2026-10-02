from __future__ import annotations

import asyncio
import socket
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from framework.config import ForgeConfig, MongoResourceConfig, ProjectConfig
from framework.security import Principal


def _request(*, method="GET", path="/", headers=None, query=b"", client="10.0.0.10", scheme="http"):
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": headers or [],
            "query_string": query,
            "server": ("test", 80),
            "client": (client, 1234),
            "scheme": scheme,
        }
    )


def _project(**overrides) -> ProjectConfig:
    data = {
        "slug": "p",
        "name": "P",
        "databases": {"primary": {"url": "sqlite+aiosqlite:///:memory:"}},
        "security": {"jwt_enabled": False},
        "roles": {"reader": {"permissions": ["notes.read"]}, "child": {"inherits": ["reader"], "permissions": ["notes.list"]}},
        "rate_limit": {"requests": 100, "window_seconds": 60, "burst": 20},
    }
    for k, v in overrides.items():
        data[k] = {**data[k], **v} if k == "security" else v
    return ProjectConfig.model_validate(data)


def test_proxy_trust_ip_host_and_https_boundaries():
    from framework.protection import client_ip, direct_peer, host_allowed, ip_allowed, request_is_https

    assert direct_peer(SimpleNamespace(client=None)) == "unknown"
    untrusted = _request(headers=[(b"x-forwarded-for", b"203.0.113.9")], client="198.51.100.10")
    assert client_ip(untrusted, ["10.0.0.0/8"]) == "198.51.100.10"
    trusted = _request(headers=[(b"x-forwarded-for", b"203.0.113.9, 10.1.1.2"), (b"x-forwarded-proto", b"https")], client="10.1.1.1")
    assert client_ip(trusted, ["10.0.0.0/8"]) == "203.0.113.9"
    assert request_is_https(trusted, ["10.0.0.0/8"])
    assert not request_is_https(untrusted, ["10.0.0.0/8"])
    assert request_is_https(_request(scheme="https"), [])
    all_trusted = _request(headers=[(b"x-forwarded-for", b"10.2.2.2, invalid, 10.3.3.3")], client="10.1.1.1")
    assert client_ip(all_trusted, ["10.0.0.0/8"]) == "10.2.2.2"
    assert ip_allowed(trusted, ["203.0.113.0/24"], [], ["10.0.0.0/8"])
    assert not ip_allowed(trusted, [], ["203.0.113.9"], ["10.0.0.0/8"])
    assert not ip_allowed(_request(client="bad-ip"), ["10.0.0.0/8"], [])
    assert ip_allowed(_request(client="bad-ip"), [], [])
    assert host_allowed("api.example.com:443", ["api.example.com"])
    assert host_allowed("a.example.com", ["*.example.com"])
    assert not host_allowed("example.com", ["*.example.com"])
    assert not host_allowed(None, ["example.com"])
    assert host_allowed("whatever", ["*"])


def test_request_body_limit_asgi_paths():
    from framework.protection import RequestBodyLimitMiddleware

    async def run_case(scope, incoming, limit=5):
        sent = []
        queue = list(incoming)

        async def receive():
            return queue.pop(0)

        async def send(message):
            sent.append(message)

        async def app(scope, receive, send):
            while True:
                msg = await receive()
                if not msg.get("more_body"):
                    break
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok", "more_body": False})

        await RequestBodyLimitMiddleware(app, lambda p: limit if p == "/limited" else None)(scope, receive, send)
        return sent

    async def run():
        base = {"type": "http", "method": "POST", "path": "/limited", "headers": []}
        assert (await run_case(base, [{"type": "http.request", "body": b"123456", "more_body": False}]))[0]["status"] == 413
        assert (
            await run_case({**base, "headers": [(b"content-length", b"99")]}, [{"type": "http.request", "body": b"", "more_body": False}])
        )[0]["status"] == 413
        assert (
            await run_case({**base, "headers": [(b"content-length", b"wat")]}, [{"type": "http.request", "body": b"", "more_body": False}])
        )[0]["status"] == 400
        assert (await run_case({**base, "path": "/free"}, [{"type": "http.request", "body": b"123456", "more_body": False}]))[0][
            "status"
        ] == 200
        sent = []

        async def app(scope, receive, send):
            sent.append(scope["type"])

        await RequestBodyLimitMiddleware(app, lambda _: 1)({"type": "websocket", "path": "/limited"}, lambda: None, lambda _: None)
        assert sent == ["websocket"]

    asyncio.run(run())


def test_security_claims_roles_local_jwt_and_delegation():
    import framework.security as sec

    assert sec._claim({"a": {"b": 1}}, "a.b") == 1
    assert sec._claim({"a": {}}, "a.b", "x") == "x"
    assert sec._claim_set(None) == set()
    assert sec._claim_set("x") == {"x"}
    assert sec._claim_set(["x", 2, None]) == {"x", "2"}
    assert sec.permission_matches("notes.*", "notes.read") and not sec.permission_matches("notes.*", "users.read")
    project = _project(security={"jwt_enabled": True, "jwt_secret": "s" * 64, "jwt_require_project_claim": True})
    assert sec._expand_role_permissions(project, {"child"}) == {"notes.read", "notes.list"}
    token = sec.issue_jwt("user-1", "p", ["child"], ["direct.x"], 5, tenant_id="t1", secret="s" * 64)

    async def auth():
        p = await sec.authenticate_request(_request(headers=[(b"authorization", f"Bearer {token}".encode())]), project, object())
        assert p.kind == "jwt" and p.tenant_id == "t1" and "notes.read" in p.permissions
        bad = sec.issue_jwt("user-1", "other", [], [], 5, secret="s" * 64)
        with pytest.raises(HTTPException):
            await sec.authenticate_request(_request(headers=[(b"authorization", f"Bearer {bad}".encode())]), project, object())
        with pytest.raises(HTTPException):
            await sec.authenticate_request(_request(headers=[(b"authorization", b"Bearer " + b"x" * 9000)]), project, object())
        assert (await sec.authenticate_request(_request(), project, object())).kind == "anonymous"

    asyncio.run(auth())
    parent = Principal(
        kind="api_key",
        subject="api-key:1:parent",
        roles={"child"},
        permissions={"notes.read", "notes.list", "direct.x", "admin.jwt.issue"},
        tenant_id="t1",
        rate_requests=50,
        rate_window_seconds=60,
        rate_burst=10,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    sec.ensure_credential_delegation(
        project,
        parent,
        roles=["child"],
        permissions=["direct.x"],
        tenant_id="t1",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        rate_requests=40,
        rate_window_seconds=60,
        rate_burst=8,
        subject=parent.subject,
    )
    bads = [
        dict(roles=["reader"], permissions=[], tenant_id="t1"),
        dict(roles=["child"], permissions=["admin.*"], tenant_id="t1"),
        dict(roles=["child"], permissions=[], tenant_id="other"),
        dict(roles=["child"], permissions=[], tenant_id="t1", rate_requests=51),
        dict(roles=["child"], permissions=[], tenant_id="t1", rate_burst=11),
        dict(roles=["child"], permissions=[], tenant_id="t1", subject="other"),
    ]
    for kw in bads:
        with pytest.raises(HTTPException):
            sec.ensure_credential_delegation(project, parent, **kw)
    with pytest.raises(HTTPException):
        sec.ensure_credential_delegation(
            project, parent, roles=["child"], permissions=[], tenant_id="t1", expires_at=datetime.now(timezone.utc) + timedelta(hours=1)
        )
    with pytest.raises(HTTPException):
        sec.ensure_credential_delegation(project, parent, roles=["child"], permissions=[], tenant_id="t1", expires_at=None)
    sec.ensure_credential_delegation(
        project,
        Principal(kind="api_key", subject="god", roles=set(), permissions={"admin.credentials.delegate_any"}),
        roles=["child"],
        permissions=["*"],
        tenant_id="x",
        subject="other",
    )


def test_http_client_stream_limits_retry_and_circuit(monkeypatch):
    from framework.services.http_client import ResilientHTTPClient, ResponseTooLarge

    assert ResilientHTTPClient._retry_delay(0, httpx.Response(429, headers={"Retry-After": "3"})) == 3
    assert ResilientHTTPClient._retry_delay(0, httpx.Response(429, headers={"Retry-After": "invalid"})) == 0.25

    async def run():
        async def handler(request):
            return httpx.Response(
                200,
                content=b"123456" if request.url.path == "/large" else b'{"ok":true}',
                headers={"content-type": "application/json"},
                request=request,
            )

        client = ResilientHTTPClient()
        await client.client.aclose()
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        assert (await client._send_once("GET", "https://x.test/ok", max_response_bytes=20)).json() == {"ok": True}
        with pytest.raises(ResponseTooLarge):
            await client._send_once("GET", "https://x.test/large", max_response_bytes=5)
        await client.close()
        retry = ResilientHTTPClient()
        attempts = []

        async def send(method, url, **kwargs):
            attempts.append(url)
            if len(attempts) < 2:
                raise httpx.ConnectError("boom", request=httpx.Request(method, url))
            return httpx.Response(200, request=httpx.Request(method, url))

        retry._send_once = send

        async def no_sleep(_):
            return None

        monkeypatch.setattr(asyncio, "sleep", no_sleep)
        assert (await retry.request("GET", "https://retry.test", retries=1)).status_code == 200
        assert len(attempts) == 2
        await retry.close()
        circuit = ResilientHTTPClient(failure_threshold=1, reset_seconds=60)

        async def fail(method, url, **kwargs):
            raise httpx.ConnectError("down", request=httpx.Request(method, url))

        circuit._send_once = fail
        with pytest.raises(httpx.ConnectError):
            await circuit.request("GET", "https://down.test", retries=0)
        with pytest.raises(RuntimeError, match="Circuit open"):
            await circuit.request("GET", "https://down.test", retries=0)
        await circuit.close()

    asyncio.run(run())


def test_http_client_egress_backend_blocks_dns_rebinding(monkeypatch):
    import httpcore

    from framework.services.http_client import ResilientHTTPClient, _AddressPolicyBackend, blocked_network_address

    assert blocked_network_address("127.0.0.1")
    assert blocked_network_address("169.254.169.254")
    assert blocked_network_address("not-an-address")
    assert not blocked_network_address("8.8.8.8")

    class Dialer:
        def __init__(self):
            self.calls = []

        async def connect_tcp(self, host, port, **kwargs):
            self.calls.append((host, port, kwargs))
            return object()

        async def sleep(self, seconds):
            return None

    async def run():
        guarded = ResilientHTTPClient()
        assert isinstance(guarded.client._transport._pool._network_backend, _AddressPolicyBackend)
        await guarded.close()

        backend = _AddressPolicyBackend(block_private_networks=True)
        dialer = Dialer()
        backend._backend = dialer
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *args, **kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
            ],
        )
        with pytest.raises(httpcore.ConnectError, match="private or non-routable"):
            await backend.connect_tcp("example.com", 443)
        assert dialer.calls == []

        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
        )
        await backend.connect_tcp("example.com", 443)
        assert dialer.calls[0][0] == "93.184.216.34"
        with pytest.raises(httpcore.UnsupportedProtocol):
            await backend.connect_unix_socket("/tmp/forbidden.sock")

        private_backend = _AddressPolicyBackend(block_private_networks=False)
        private_dialer = Dialer()
        private_backend._backend = private_dialer
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))],
        )
        await private_backend.connect_tcp("internal.example", 80)
        assert private_dialer.calls[0][0] == "127.0.0.1"

    asyncio.run(run())


def test_mongo_owner_tenant_and_query_paths():
    from framework.mongo import _base_filter, _positive_int, _query_filter, _visible

    resource = MongoResourceConfig.model_validate(
        {
            "database": "main",
            "collection": "items",
            "path": "items",
            "tenant_field": "tenant_id",
            "owner_field": "owner_id",
            "owner_actions": ["list", "read", "update", "delete"],
            "owner_bypass_permission": "items.owner_bypass",
            "soft_delete_field": "deleted_at",
            "writable_fields": ["name"],
            "allowed_filters": ["name"],
            "filter_operators": ["eq", "ne", "in"],
            "allowed_sort": ["name"],
        }
    )
    p = Principal(kind="api_key", subject="u1", roles=set(), permissions=set(), tenant_id="t1")
    assert _base_filter(resource, p) == {"tenant_id": "t1", "owner_id": "u1", "deleted_at": None}
    bypass = Principal(kind="api_key", subject="admin", roles=set(), permissions={"items.owner_bypass"}, tenant_id="t1")
    assert "owner_id" not in _base_filter(resource, bypass)
    with pytest.raises(HTTPException):
        _base_filter(resource, Principal(kind="anonymous", subject="x", roles=set(), permissions=set(), tenant_id="t1"))
    assert _query_filter(_request(query=b"name__in=a,b&ignored=x"), resource, p)["name"] == {"$in": ["a", "b"]}
    with pytest.raises(HTTPException):
        _positive_int(_request(query=b"limit=no"), "limit", 10, minimum=1)
    with pytest.raises(HTTPException):
        _positive_int(_request(query=b"limit=0"), "limit", 10, minimum=1)
    assert _visible({"_id": 123, "secret": 1}, MongoResourceConfig(database="m", collection="x", path="x", hidden_fields=["secret"])) == {
        "_id": "123"
    }


def test_runtime_manager_transactional_start_and_cleanup(monkeypatch):
    import framework.runtime as rt

    projects = [_project(slug="a", name="A", api_prefix="/a"), _project(slug="b", name="B", api_prefix="/b")]
    forge = ForgeConfig(projects=projects)
    created = []

    class Service:
        def __init__(self, name):
            self.name = name
            self.closed = False

        async def close(self):
            self.closed = True

        async def dispose(self):
            self.closed = True

        async def ping(self):
            return True

    async def registry(cfg):
        if cfg.slug == "b" and getattr(registry, "fail", False):
            raise RuntimeError("boom")
        s = Service("db" + cfg.slug)
        created.append(s)
        return s

    async def mongo(cfg):
        s = Service("mongo" + cfg.slug)
        created.append(s)
        return s

    monkeypatch.setattr(rt, "build_registry", registry)
    monkeypatch.setattr(rt, "build_mongo_registry", mongo)
    monkeypatch.setattr(rt, "build_cache", lambda *a, **k: Service("cache"))
    monkeypatch.setattr(rt, "DataSourceManager", lambda cfg: Service("data"))
    monkeypatch.setattr(rt, "build_event_hub", lambda *a, **k: Service("events"))
    monkeypatch.setattr(rt, "MemoryRateLimiter", lambda **k: Service("limiter"))

    async def run():
        m = rt.RuntimeManager(forge)
        assert m.for_path("/a/x").config.slug == "a" and m.for_path("/none") is None
        await m.start()
        await m.close()
        assert all(s.closed for s in created)
        created.clear()
        registry.fail = True
        m = rt.RuntimeManager(forge)
        await m.start()
        assert m.runtimes["a"].available and not m.runtimes["b"].available
        assert m.runtimes["b"].error_type == "RuntimeError"
        assert not any(s.closed for s in created)
        await m.close()
        assert all(s.closed for s in created)

    asyncio.run(run())
