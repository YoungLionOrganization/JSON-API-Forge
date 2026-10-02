from __future__ import annotations

import uuid
from types import SimpleNamespace

from fastapi import Request
from fastapi.testclient import TestClient

from framework.config import ForgeConfig, ProjectConfig
from framework.protection import ConcurrencyGate
from framework.security import Principal


def _project(**overrides):
    data = {
        "slug": "p",
        "name": "P",
        "api_prefix": "/api/p/v1",
        "databases": {"primary": {"url": "sqlite+aiosqlite:///:memory:"}},
        "security": {"jwt_enabled": False},
        "cors_origins": ["https://client.example"],
        "cors_methods": ["GET", "POST", "OPTIONS"],
        "cors_headers": ["X-API-Key", "Content-Type", "X-Request-ID"],
        "cors_expose_headers": ["X-Request-ID", "X-Forge-Cache"],
        "rate_limit": {
            "enabled": True,
            "pre_auth_enabled": True,
            "pre_auth_requests": 1000,
            "pre_auth_window_seconds": 60,
            "requests": 100,
            "window_seconds": 60,
            "route_requests": 50,
            "route_window_seconds": 60,
        },
        "protection": {
            "max_request_body_bytes": 1024,
            "trusted_hosts": ["api.example"],
            "trusted_proxy_cidrs": ["10.0.0.0/8"],
            "gzip_minimum_size": 1024,
            "request_timeout_seconds": 2.0,
        },
        "observability": {"metrics_enabled": True, "metrics_path": "/metrics"},
    }
    for k, v in overrides.items():
        data[k] = {**data[k], **v} if k in {"security", "protection", "rate_limit", "observability"} else v
    return ProjectConfig.model_validate(data)


class FakeLimiter:
    def __init__(self):
        self.calls = []
        self.fail = None

    async def check(self, identity, limit, window, burst=None):
        self.calls.append((identity, limit, window, burst))
        if self.fail:
            raise self.fail

    async def ping(self):
        return True

    async def close(self):
        pass


class FakeConn:
    def __init__(self, fail=False):
        self.fail = fail

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt):
        if self.fail:
            raise RuntimeError("db down")


class FakeDbEngine:
    def __init__(self, fail=False):
        self.fail = fail
        self.disposed = False

    def connect(self):
        return FakeConn(self.fail)

    async def dispose(self):
        self.disposed = True


class FakeAudit:
    def __init__(self, engine):
        self.engine = engine
        self.items = []
        self.started = False
        self.closed = False

    async def start(self):
        self.started = True

    def submit(self, **item):
        self.items.append(item)

    async def close(self):
        self.closed = True


class FakeManager:
    last = None

    def __init__(self, forge, reuse=None):
        from framework.runtime import ProjectRuntime

        self.forge = forge
        self.runtimes = {}
        for project in forge.projects:
            runtime = ProjectRuntime(config=project)
            runtime.limiter = FakeLimiter()
            runtime.gate = ConcurrencyGate(8, 0.2, True)
            runtime.registry = SimpleNamespace(engines={"primary": FakeDbEngine()})
            runtime.mongo_registry = None
            runtime.cache = SimpleNamespace(ping=self._ok)
            runtime.event_hub = SimpleNamespace(ping=self._ok)
            self.runtimes[project.slug] = runtime
        self.started = False
        self.closed = False
        FakeManager.last = self

    async def _ok(self):
        return True

    def for_path(self, path):
        for r in self.runtimes.values():
            p = r.config.api_prefix.rstrip("/")
            if path == p or path.startswith(p + "/"):
                return r

    def body_limit_for_path(self, path):
        r = self.for_path(path)
        return r.config.protection.max_request_body_bytes if r else None

    async def start(self):
        self.started = True
        for runtime in self.runtimes.values():
            runtime.available = not runtime.error_type

    async def close(self):
        self.closed = True


def _install(monkeypatch, project):
    import framework.factory as factory

    forge = ForgeConfig(projects=[project])
    internal = FakeDbEngine()
    monkeypatch.setattr(factory, "load_config_resilient", lambda *_a, **_k: (forge, {}))
    monkeypatch.setattr(factory, "ensure_no_errors", lambda *_a, **_k: [])
    monkeypatch.setattr(factory, "project_diagnostics", lambda *_a, **_k: [])
    monkeypatch.setattr(factory, "expand_feature_packs", lambda _p: None)
    monkeypatch.setattr(factory, "RuntimeManager", FakeManager)
    monkeypatch.setattr(factory, "create_async_engine", lambda *_a, **_k: internal)

    async def init_security(*a, **k):
        pass

    monkeypatch.setattr(factory, "init_security", init_security)
    monkeypatch.setattr(factory, "AuditWriter", FakeAudit)
    monkeypatch.setattr(factory, "metrics_payload", lambda: (b"forge_metric 1\n", "text/plain"))
    monkeypatch.setattr(factory, "observe", lambda *a, **k: None)

    async def auth(request, cfg, engine):
        key = request.headers.get("X-API-Key")
        if key == "good":
            return Principal(kind="api_key", subject="api-key:1:good", roles=set(), permissions={"probe.read"})
        if key == "limited":
            return Principal(
                kind="api_key",
                subject="api-key:2:limited",
                roles=set(),
                permissions=set(),
                rate_requests=7,
                rate_window_seconds=30,
                rate_burst=2,
            )
        return Principal(kind="anonymous", subject="anonymous", roles=set(), permissions=set())

    monkeypatch.setattr(factory, "authenticate_request", auth)

    def register_routes(*, app, runtime, principal_for, require, **kwargs):
        prefix = runtime.config.api_prefix.rstrip("/")

        @app.get(prefix + "/probe")
        async def probe(request: Request):
            p = await principal_for(request, runtime)
            require(p, "probe.read")
            request.state.forge_cache = "hit"
            request.state.forge_idempotent_replay = True
            return {"subject": p.subject}

        @app.get(prefix + "/private")
        async def private(request: Request):
            p = await principal_for(request, runtime)
            require(p, "probe.read")
            return {"ok": True}

        @app.post(prefix + "/echo")
        async def echo(request: Request):
            await principal_for(request, runtime)
            return {"size": len(await request.body())}

    monkeypatch.setattr(factory, "register_project_routes", register_routes)
    monkeypatch.setattr(factory.settings, "internal_database_url", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setattr(factory.settings, "internal_schema_mode", "create")
    monkeypatch.setattr(factory.settings, "operator_token", "operator-secret")
    monkeypatch.setattr(factory.settings, "app_env", "development")
    return factory, internal


def test_factory_http_security_cors_headers_rate_limits_and_global_docs(monkeypatch):
    factory, internal = _install(monkeypatch, _project())
    app = factory.create_app(apps_dir="unused")
    with TestClient(app, base_url="http://api.example") as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/docs").status_code == 404
        assert client.get("/api/p/v1/private").status_code == 401
        assert client.get("/api/p/v1/private", headers={"X-API-Key": "limited"}).status_code == 403
        r = client.get("/api/p/v1/probe", headers={"X-API-Key": "good", "Origin": "https://client.example", "X-Request-ID": "req-123"})
        assert (
            r.status_code == 200
            and r.headers["x-request-id"] == "req-123"
            and r.headers["x-forge-cache"] == "hit"
            and r.headers["x-forge-idempotent-replay"] == "true"
            and r.headers["access-control-allow-origin"] == "https://client.example"
        )
        unsafe_id = client.get(
            "/api/p/v1/probe",
            headers={"X-API-Key": "good", "X-Request-ID": "bad id/with spaces"},
        ).headers["x-request-id"]
        assert unsafe_id != "bad id/with spaces"
        uuid.UUID(unsafe_id)
        pre = client.options(
            "/api/p/v1/probe",
            headers={
                "Origin": "https://client.example",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-API-Key",
            },
        )
        assert pre.status_code == 204
        assert (
            client.options(
                "/api/p/v1/probe", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"}
            ).status_code
            == 403
        )
        assert client.get("/api/p/v1/probe", headers={"host": "other.example", "X-API-Key": "good"}).status_code == 400
        assert client.post("/api/p/v1/echo", content=b"x" * 2048, headers={"X-API-Key": "good"}).status_code == 413
        calls = FakeManager.last.runtimes["p"].limiter.calls
        ids = [x[0] for x in calls]
        assert any(":preauth:ip:" in x for x in ids) and "p:api_key:api-key:1:good:global" in ids
        assert client.get("/metrics").status_code == 401
        assert client.get("/metrics", headers={"X-Forge-Operator-Token": "operator-secret"}).status_code == 200
    assert FakeManager.last.started and FakeManager.last.closed and internal.disposed


def test_factory_readiness_redaction_https_and_limiter_failure(monkeypatch):
    factory, _ = _install(monkeypatch, _project(security={"require_https": True}, rate_limit={"fail_open": False}))
    app = factory.create_app(apps_dir="unused")
    with TestClient(app, base_url="http://api.example") as client:
        assert client.get("/api/p/v1/probe", headers={"X-API-Key": "good"}).status_code == 400
        runtime = FakeManager.last.runtimes["p"]
        runtime.registry.engines["primary"].fail = True
        assert client.get("/ready").status_code == 503
        d = client.get("/ready", headers={"Authorization": "Bearer operator-secret"})
        assert d.status_code == 503 and d.json()["projects"]["p"]["databases"]["primary"] == "error:RuntimeError"
        runtime.registry.engines["primary"].fail = False
        runtime.config.security.require_https = False
        runtime.limiter.fail = RuntimeError("redis down")
        u = client.get("/api/p/v1/probe", headers={"X-API-Key": "good"})
        assert u.status_code == 503 and u.headers["retry-after"] == "1"


def test_factory_fails_open_limiter_and_process_wide_config_conflicts(monkeypatch):
    factory, _ = _install(monkeypatch, _project(rate_limit={"fail_open": True}))
    app = factory.create_app(apps_dir="unused")
    with TestClient(app, base_url="http://api.example") as client:
        FakeManager.last.runtimes["p"].limiter.fail = RuntimeError("down")
        assert client.get("/api/p/v1/probe", headers={"X-API-Key": "good"}).status_code == 200
    p1 = _project(slug="a", name="A", api_prefix="/api/a/v1", protection={"gzip_minimum_size": 100}, observability={"metrics_path": "/m"})
    p2 = _project(slug="b", name="B", api_prefix="/api/b/v1", protection={"gzip_minimum_size": 200}, observability={"metrics_path": "/m"})
    monkeypatch.setattr(factory, "load_config_resilient", lambda *_a, **_k: (ForgeConfig(projects=[p1, p2]), {}))
    with TestClient(factory.create_app(apps_dir="unused"), base_url="http://api.example") as client:
        assert client.get("/api/a/v1/probe", headers={"X-API-Key": "good"}).status_code == 200
        assert client.get("/api/b/v1/probe", headers={"X-API-Key": "good"}).status_code == 503
        assert FakeManager.last.runtimes["b"].error_type == "SharedMiddlewareConflict"
