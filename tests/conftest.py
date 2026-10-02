from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from framework.settings import settings


@pytest.fixture
def integration_client(tmp_path: Path, monkeypatch):
    pytest.importorskip("aiosqlite", reason="integration lifecycle tests require the async SQLite driver from requirements.txt")
    apps = tmp_path / "app"
    project = apps / "TestApp"
    (project / "data").mkdir(parents=True)
    (project / "config").mkdir()
    (project / "hooks").mkdir()
    (project / "hooks" / "__init__.py").write_text("", encoding="utf-8")
    (project / "data" / "catalog.json").write_text('[{"id":1,"name":"one"}]\n', encoding="utf-8")
    business_db = tmp_path / "business.db"
    internal_db = tmp_path / "internal.db"
    media_dir = tmp_path / "media"
    bootstrap = "B" * 48
    config = {
        "slug": "test",
        "name": "Test App",
        "api_prefix": "/api/test/v1",
        "docs_enabled": True,
        "audit_enabled": True,
        "cors_origins": ["https://example.test"],
        "databases": {"primary": {"url": f"sqlite+aiosqlite:///{business_db}"}},
        "security": {"bootstrap_enabled": True, "bootstrap_admin_key": bootstrap, "bootstrap_one_time": True, "jwt_enabled": False},
        "rate_limit": {"enabled": True, "backend": "memory", "requests": 1000, "window_seconds": 60, "route_requests": 1000},
        "cache": {"enabled": True, "backend": "memory", "default_ttl_seconds": 10},
        "protection": {
            "max_request_body_bytes": 1024,
            "max_concurrent_requests": 8,
            "request_timeout_seconds": 10,
            "trusted_hosts": ["testserver"],
            "gzip_minimum_size": 100000,
            "reject_when_saturated": False,
            "max_queue_wait_seconds": 0.2,
        },
        "roles": {"admin": {"permissions": ["*"]}},
        "resources": [
            {
                "database": "primary",
                "table": "accounts",
                "path": "accounts",
                "auto_create": True,
                "columns": {
                    "id": {"type": "integer", "primary_key": True, "nullable": False},
                    "user_id": {"type": "string", "nullable": False, "unique": True, "max_length": 64},
                    "balance": {"type": "integer", "nullable": False, "default": 0},
                },
                "writable_fields": ["user_id", "balance"],
                "allowed_filters": ["user_id", "balance"],
                "filter_operators": ["eq", "gt", "gte", "lt", "lte"],
                "allowed_sort": ["id", "balance"],
                "permissions": {
                    "list": "accounts.list",
                    "read": "accounts.read",
                    "create": "accounts.create",
                    "update": "accounts.update",
                    "delete": "accounts.delete",
                },
            }
        ],
        "operations": [
            {
                "name": "transfer",
                "path": "rpc/transfer",
                "method": "POST",
                "database": "primary",
                "permission": "economy.transfer",
                "transaction": True,
                "idempotency": True,
                "input_schema": {
                    "type": "object",
                    "required": ["from_user", "to_user", "amount"],
                    "additionalProperties": False,
                    "properties": {
                        "from_user": {"type": "string"},
                        "to_user": {"type": "string"},
                        "amount": {"type": "integer", "minimum": 1},
                    },
                },
                "statements": [
                    {
                        "sql": "UPDATE accounts SET balance = balance - :amount WHERE user_id = :from_user AND balance >= :amount",
                        "mode": "execute",
                        "params": {"amount": "$body.amount", "from_user": "$body.from_user"},
                        "require_rowcount_min": 1,
                        "require_rowcount_max": 1,
                        "result_name": "debit",
                    },
                    {
                        "sql": "UPDATE accounts SET balance = balance + :amount WHERE user_id = :to_user",
                        "mode": "execute",
                        "params": {"amount": "$body.amount", "to_user": "$body.to_user"},
                        "require_rowcount_min": 1,
                        "require_rowcount_max": 1,
                        "result_name": "credit",
                    },
                ],
                "invalidate_resources": ["accounts"],
            }
        ],
        "data_sources": [
            {
                "name": "catalog",
                "path": "catalog",
                "type": "json_file",
                "file": "data/catalog.json",
                "permission": "catalog.access",
                "writable": True,
                "id_field": "id",
            },
            {"name": "public-info", "path": "public/info", "type": "static", "public": True, "data": {"status": "public"}},
        ],
        "event_channels": [
            {
                "name": "notifications",
                "path": "events/notifications",
                "publish_permission": "events.publish",
                "subscribe_permission": "events.subscribe",
                "websocket_enabled": True,
                "sse_enabled": True,
                "websocket_message_requests": 10,
            }
        ],
        "media": {
            "enabled": True,
            "backend": "local",
            "local_directory": str(media_dir),
            "max_upload_bytes": 512,
            "allowed_mime_types": ["text/plain"],
            "public": False,
            "upload_permission": "media.upload",
            "read_permission": "media.read",
            "delete_permission": "media.delete",
            "admin_permission": "media.admin",
        },
    }
    (project / "app.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    monkeypatch.setattr(settings, "apps_dir", apps)
    monkeypatch.setattr(settings, "internal_database_url", f"sqlite+aiosqlite:///{internal_db}")
    monkeypatch.setattr(settings, "jwt_secret", "J" * 64)
    monkeypatch.setattr(settings, "bootstrap_admin_key", "")
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "redis_url", None)
    from framework.factory import create_app

    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/test/v1/admin/api-keys", headers={"X-API-Key": bootstrap}, json={"name": "integration-admin", "permissions": ["*"]}
        )
        assert response.status_code == 200, response.text
        admin_key = response.json()["api_key"]
        assert response.json()["bootstrap_consumed"] is True
        yield client, admin_key, bootstrap, project
