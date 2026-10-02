from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from framework.config import ProjectConfig
from framework.factory import _hidden_route, _hide_internal_signature, _invoke_hook
from framework.routers.project import register_project_routes
from framework.security import Principal


class FakeCache:
    def __init__(self):
        self.invalidated = []
        self.keys = []

    async def make_key(self, namespace, value):
        self.keys.append((namespace, value))
        return namespace + ":key"

    async def get_or_set_json(self, key, ttl, loader, stale=None):
        return await loader(), True

    async def invalidate_namespace(self, namespace):
        self.invalidated.append(namespace)


class FakeDataSources:
    def __init__(self):
        self.calls = []

    async def read(self, source, request, payload):
        self.calls.append(("read", source.name, payload))
        return {"source": source.name, "query": dict(request.query_params)}

    async def create(self, source, payload):
        self.calls.append(("create", source.name, payload))
        return {"id": "new", **payload}

    async def update(self, source, item_id, payload, *, replace=False):
        self.calls.append(("update", item_id, replace, payload))
        return {"id": item_id, "replace": replace, **payload}

    async def delete(self, source, item_id):
        self.calls.append(("delete", item_id))
        return True


class FakeEventHub:
    def __init__(self):
        self.events = []

    async def publish(self, channel, event):
        self.events.append((channel, event))
        return 2


class FakeStore:
    def __init__(self, path: Path):
        self.path = path

    def path_for(self, storage_key):
        return self.path


class FakeLimiter:
    async def check(self, *args, **kwargs):
        return None


async def _principal_for(request, runtime):
    p = Principal(kind="api_key", subject="api-key:7:test", roles={"admin"}, permissions={"*"}, tenant_id="t1")
    request.state.principal = p
    if not hasattr(request.state, "request_id"):
        request.state.request_id = "router-test"
    return p


def _require(principal, permission):
    if permission == "always.deny":
        raise HTTPException(status_code=403, detail="denied")


def _project(tmp_path: Path) -> ProjectConfig:
    return ProjectConfig.model_validate(
        {
            "slug": "p",
            "name": "Project",
            "api_prefix": "/api/p/v1",
            "docs_enabled": True,
            "databases": {"primary": {"url": "sqlite+aiosqlite:///:memory:"}},
            "mongo_databases": {"mongo": {"uri": "mongodb://localhost:27017", "database": "p"}},
            "security": {"jwt_enabled": True, "jwt_provider": "local_hs256", "jwt_secret": "J" * 64},
            "cache": {"backend": "memory", "cache_lists": True, "cache_reads": True},
            "rate_limit": {"enabled": False},
            "resources": [
                {
                    "database": "primary",
                    "table": "notes",
                    "path": "notes",
                    "batch_enabled": True,
                    "permissions": {
                        "list": "notes.list",
                        "read": "notes.read",
                        "create": "notes.create",
                        "update": "notes.update",
                        "delete": "notes.delete",
                    },
                    "cache": {"enabled": True},
                }
            ],
            "mongo_resources": [
                {
                    "database": "mongo",
                    "collection": "profiles",
                    "path": "profiles",
                    "permissions": {
                        "list": "profiles.list",
                        "read": "profiles.read",
                        "create": "profiles.create",
                        "update": "profiles.update",
                        "delete": "profiles.delete",
                    },
                    "cache": {"enabled": True},
                }
            ],
            "operations": [
                {
                    "name": "lookup",
                    "path": "rpc/lookup",
                    "method": "GET",
                    "permission": "rpc.lookup",
                    "transaction": False,
                    "statements": [{"sql": "SELECT 1", "mode": "scalar", "result_name": "value"}],
                    "cache": {"enabled": True, "ttl_seconds": 5},
                    "invalidate_resources": ["notes"],
                },
                {
                    "name": "grant",
                    "path": "rpc/grant",
                    "method": "POST",
                    "permission": "rpc.grant",
                    "transaction": True,
                    "idempotency": True,
                    "statements": [{"sql": "UPDATE x SET y=1", "mode": "execute"}],
                    "invalidate_resources": ["notes"],
                    "invalidate_operations": ["lookup"],
                },
            ],
            "data_sources": [
                {
                    "name": "catalog",
                    "type": "static",
                    "path": "data/catalog",
                    "data": [],
                    "permission": "data.read",
                    "writable": True,
                    "write_permission": "data.write",
                    "cache_ttl_seconds": 5,
                }
            ],
            "custom_endpoints": [
                {
                    "path": "custom/echo",
                    "method": "POST",
                    "permission": "custom.echo",
                    "handler": "fake.echo",
                    "input_mode": "json",
                    "response": {"kind": "json"},
                },
                {
                    "path": "custom/public",
                    "method": "GET",
                    "public": True,
                    "handler": "fake.public",
                    "input_mode": "none",
                    "response": {"kind": "text"},
                },
            ],
            "event_channels": [
                {
                    "name": "updates",
                    "path": "events/updates",
                    "publish_permission": "events.publish",
                    "subscribe_permission": "events.subscribe",
                    "sse_enabled": False,
                    "websocket_enabled": False,
                    "max_message_bytes": 1024,
                }
            ],
            "media": {
                "enabled": True,
                "backend": "local",
                "local_directory": str(tmp_path / "media"),
                "signed_urls_enabled": True,
                "signing_secret": "M" * 64,
                "owner_delete_only": True,
            },
            "webhook_docs": [{"name": "incoming", "method": "POST", "payload_schema": {"type": "object"}}],
        }
    )


def _build_app(monkeypatch, tmp_path):
    import framework.routers.project as router

    project = _project(tmp_path)
    cache = FakeCache()
    data_sources = FakeDataSources()
    event_hub = FakeEventHub()
    media_path = tmp_path / "media-object.bin"
    media_path.write_bytes(b"hello")
    runtime = SimpleNamespace(
        config=project,
        registry=SimpleNamespace(engines={"primary": object()}, tables={("primary", "notes"): object()}),
        mongo_registry=SimpleNamespace(databases={"mongo": object()}),
        cache=cache,
        data_sources=data_sources,
        event_hub=event_hub,
        media_store=FakeStore(media_path),
        limiter=FakeLimiter(),
    )

    async def list_rows(request, engine, table, resource, principal):
        return {"items": [{"id": 1}], "limit": 50}

    async def count_rows(request, engine, table, resource, principal):
        return {"count": 1}

    async def get_row(engine, table, resource, principal, item_id):
        return {"id": item_id, "kind": "sql"}

    async def create_row(engine, table, resource, principal, payload):
        return {"id": 2, **payload}

    async def batch_rows(engine, table, resource, principal, payload):
        return [{"id": i + 1, **v} for i, v in enumerate(payload)]

    async def update_row(engine, table, resource, principal, item_id, payload, *, replace=False):
        return {"id": item_id, "replace": replace, **payload}

    async def delete_row(engine, table, resource, principal, item_id):
        return True

    for n, fn in {
        "list_rows": list_rows,
        "count_rows": count_rows,
        "get_row": get_row,
        "create_row": create_row,
        "batch_create_rows": batch_rows,
        "update_row": update_row,
        "delete_row": delete_row,
    }.items():
        monkeypatch.setattr(router, n, fn)

    async def list_documents(request, db, resource, principal):
        return {"items": [{"id": "m1"}]}

    async def count_documents(request, db, resource, principal):
        return {"count": 1}

    async def get_document(db, resource, principal, item_id):
        return {"id": item_id, "kind": "mongo"}

    async def create_document(db, resource, principal, payload):
        return {"id": "m2", **payload}

    async def update_document(db, resource, principal, item_id, payload, *, replace=False):
        return {"id": item_id, "replace": replace, **payload}

    async def delete_document(db, resource, principal, item_id):
        return True

    for n, fn in {
        "list_documents": list_documents,
        "count_documents": count_documents,
        "get_document": get_document,
        "create_document": create_document,
        "update_document": update_document,
        "delete_document": delete_document,
    }.items():
        monkeypatch.setattr(router, n, fn)

    async def execute_operation(engine, op, *, body, request, principal):
        return {"operation": op.name, "payload": body}

    async def execute_idempotent_operation(engine, **kwargs):
        return ({"operation": kwargs["operation"].name}, kwargs["raw_key"] == "replay")

    monkeypatch.setattr(router, "execute_operation", execute_operation)
    monkeypatch.setattr(router, "execute_idempotent_operation", execute_idempotent_operation)
    monkeypatch.setattr(router, "operation_input_context", lambda **kwargs: {"input": "ctx"})
    monkeypatch.setattr(router, "ensure_credential_delegation", lambda *a, **k: None)

    async def create_key(*a, **k):
        return {"id": 1, "key": "secret", "name": k["name"]}

    async def list_keys(*a, **k):
        return [{"id": 1, "name": "one"}]

    async def get_key(*a, **k):
        return {
            "id": 1,
            "name": "one",
            "prefix": "jf2_one",
            "roles": [],
            "permissions": [],
            "tenant_id": None,
            "enabled": True,
            "rate_requests": None,
            "rate_window_seconds": None,
            "rate_burst": None,
            "expires_at": None,
            "created_at": None,
        }

    async def revoke_key(*a, **k):
        return True

    monkeypatch.setattr(router, "create_api_key", create_key)
    monkeypatch.setattr(router, "list_api_keys", list_keys)
    monkeypatch.setattr(router, "get_api_key_metadata", get_key)
    monkeypatch.setattr(router, "revoke_api_key", revoke_key)
    monkeypatch.setattr(router, "issue_jwt", lambda *a, **k: "jwt-token")

    async def custom_hook(**kwargs):
        return {"echo": kwargs["payload"], "subject": kwargs["principal"].subject}

    async def public_hook(**kwargs):
        return "public-ok"

    monkeypatch.setattr(router, "import_callable", lambda path: public_hook if path == "fake.public" else custom_hook)
    media_meta = {
        "id": "media1",
        "project_slug": "p",
        "owner_subject": "api-key:7:test",
        "original_name": "x.bin",
        "content_type": "application/octet-stream",
        "size": 5,
        "sha256": "abc",
        "storage_key": "obj-key",
        "created_at": None,
    }

    async def save_media(**kwargs):
        return dict(media_meta)

    async def get_media_meta(*args):
        return dict(media_meta)

    async def delete_media(*args):
        return True

    monkeypatch.setattr(router, "save_media", save_media)
    monkeypatch.setattr(router, "get_media_meta", get_media_meta)
    monkeypatch.setattr(router, "delete_media", delete_media)
    app = FastAPI()
    app.state.internal_engine = object()
    register_project_routes(
        app=app,
        runtime=runtime,
        principal_for=_principal_for,
        require=_require,
        hide_internal_signature=_hide_internal_signature,
        hidden_route=_hidden_route,
        invoke_hook=_invoke_hook,
    )
    return app, runtime


def test_router_meta_docs_admin_and_generated_openapi(monkeypatch, tmp_path):
    app, _ = _build_app(monkeypatch, tmp_path)
    client = TestClient(app)
    meta = client.get("/api/p/v1/meta")
    assert meta.status_code == 200 and meta.json()["project"] == "p"
    assert {x["backend"] for x in meta.json()["resources"]} == {"sql", "mongodb"}
    schema = client.get("/api/p/v1/_openapi.json")
    assert schema.status_code == 200 and schema.json()["info"]["title"] == "Project API"
    assert all(path.startswith("/api/p/v1/") for path in schema.json()["paths"])
    assert client.get("/api/p/v1/_docs").status_code == 200 and client.get("/api/p/v1/_redoc").status_code == 200
    created = client.post("/api/p/v1/admin/api-keys", json={"name": "plugin", "permissions": ["notes.read"]})
    assert created.status_code == 200
    assert client.get("/api/p/v1/admin/api-keys").json()["items"][0]["id"] == 1
    assert client.delete("/api/p/v1/admin/api-keys/1").json() == {"revoked": True}
    jwt = client.post("/api/p/v1/admin/jwt", json={"subject": "user-1", "permissions": ["notes.read"]})
    assert jwt.json()["token"] == "jwt-token"
    assert "p.incoming" in app.openapi().get("webhooks", {})


def test_router_sql_and_mongo_crud_cache_and_replace_semantics(monkeypatch, tmp_path):
    app, runtime = _build_app(monkeypatch, tmp_path)
    client = TestClient(app)
    assert client.get("/api/p/v1/notes").json()["items"][0]["id"] == 1
    assert client.get("/api/p/v1/notes/_count").json() == {"count": 1}
    assert client.get("/api/p/v1/notes/7").json()["kind"] == "sql"
    assert client.post("/api/p/v1/notes", json={"title": "x"}).json()["title"] == "x"
    assert len(client.post("/api/p/v1/notes/_batch", json=[{"x": 1}, {"x": 2}]).json()) == 2
    assert client.patch("/api/p/v1/notes/7", json={"x": 1}).json()["replace"] is False
    assert client.put("/api/p/v1/notes/7", json={"x": 1}).json()["replace"] is True
    assert client.delete("/api/p/v1/notes/7").json() is True
    assert client.get("/api/p/v1/profiles").json()["items"][0]["id"] == "m1"
    assert client.get("/api/p/v1/profiles/_count").json() == {"count": 1}
    assert client.get("/api/p/v1/profiles/m1").json()["kind"] == "mongo"
    assert client.post("/api/p/v1/profiles", json={"name": "n"}).json()["name"] == "n"
    assert client.patch("/api/p/v1/profiles/m1", json={"x": 1}).json()["replace"] is False
    assert client.put("/api/p/v1/profiles/m1", json={"x": 1}).json()["replace"] is True
    assert client.delete("/api/p/v1/profiles/m1").json() is True
    assert any(ns.endswith(":notes") for ns in runtime.cache.invalidated)
    assert any("mongo:mongo:profiles" in ns for ns in runtime.cache.invalidated)


def test_router_rpc_data_custom_and_events(monkeypatch, tmp_path):
    app, runtime = _build_app(monkeypatch, tmp_path)
    client = TestClient(app)
    assert client.get("/api/p/v1/rpc/lookup").json()["operation"] == "lookup"
    assert client.post("/api/p/v1/rpc/grant", json={"amount": 1}).status_code == 400
    grant = client.post("/api/p/v1/rpc/grant", json={"amount": 1}, headers={"Idempotency-Key": "abc"})
    assert grant.json()["operation"] == "grant"
    replay = client.post("/api/p/v1/rpc/grant", json={"amount": 1}, headers={"Idempotency-Key": "replay"})
    assert replay.status_code == 200
    assert replay.json()["_idempotent_replay"] is True
    assert client.get("/api/p/v1/data/catalog?q=x").json()["source"] == "catalog"
    assert client.post("/api/p/v1/data/catalog", json={"name": "n"}).json()["id"] == "new"
    assert client.patch("/api/p/v1/data/catalog/a", json={"x": 1}).json()["replace"] is False
    assert client.put("/api/p/v1/data/catalog/a", json={"x": 1}).json()["replace"] is True
    assert client.delete("/api/p/v1/data/catalog/a").json() is True
    custom = client.post("/api/p/v1/custom/echo", json={"hello": "world"})
    assert custom.json()["echo"] == {"hello": "world"}
    assert client.get("/api/p/v1/custom/public").text == "public-ok"
    published = client.post("/api/p/v1/events/updates", json={"x": 1})
    assert published.json() == {"published": True, "fanout_hint": 2, "delivery": "best-effort"}
    assert client.post("/api/p/v1/events/updates", json={"x": "z" * 2000}).status_code == 413 and runtime.event_hub.events


def test_router_media_safe_metadata_batch_signed_download_and_delete(monkeypatch, tmp_path):
    app, _ = _build_app(monkeypatch, tmp_path)
    client = TestClient(app)
    upload = client.post("/api/p/v1/media", files={"file": ("a.bin", b"hello", "application/octet-stream")})
    assert upload.status_code == 200 and "storage_key" not in upload.json()
    batch = client.post(
        "/api/p/v1/media/_batch",
        files=[("files", ("a.bin", b"a", "application/octet-stream")), ("files", ("b.bin", b"b", "application/octet-stream"))],
    )
    assert batch.status_code == 200 and batch.json()["succeeded"] == 2
    meta = client.get("/api/p/v1/media/media1/meta")
    assert meta.status_code == 200 and "storage_key" not in meta.json()
    signed = client.post("/api/p/v1/media/media1/signed-url")
    token = signed.json()["path"].split("token=", 1)[1]
    download = client.get(f"/api/p/v1/media/media1?token={token}")
    assert download.status_code == 200 and download.content == b"hello"
    assert client.delete("/api/p/v1/media/media1").json() == {"deleted": True}
