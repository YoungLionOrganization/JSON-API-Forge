from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest

from framework.cli import cmd_init, cmd_new, cmd_schema
from framework.config import ProjectConfig
from framework.doctor import project_diagnostics, route_shadows


def _args(root: Path, **values):
    return argparse.Namespace(root=str(root), **values)


def test_cli_new_init_force_preserves_user_env_and_generates_schema(tmp_path, monkeypatch):
    monkeypatch.setattr("framework.cli._secret", lambda: "R4nd0m_" * 10)
    cmd_new(_args(tmp_path, name="Bot Service", slug="bot", preset="discord-bot"))
    project = tmp_path / "app" / "Bot Service"
    assert (project / "config" / "50-bot.json").exists()
    assert json.loads((project / "app.json").read_text())["api_prefix"] == "/api/bot/v1"
    cmd_init(_args(tmp_path, production=True, force=False))
    env = tmp_path / ".env"
    text = env.read_text()
    assert "BOT_BOOTSTRAP_ADMIN_KEY=" in text and "OPERATOR_TOKEN=" in text and "APP_ENV=production" in text
    if os.name != "nt":
        assert env.stat().st_mode & 0o777 == 0o600
    env.write_text(text + '# custom stays exactly\nCUSTOM_URL="postgres://u:p#frag@host/db"\n', encoding="utf-8")
    monkeypatch.setattr("framework.cli._secret", lambda: "N3w_R4nd0m_" * 8)
    cmd_init(_args(tmp_path, production=True, force=True))
    after = env.read_text()
    assert '# custom stays exactly\nCUSTOM_URL="postgres://u:p#frag@host/db"\n' in after and "N3w_R4nd0m_" in after
    with pytest.raises(SystemExit, match="Refusing to overwrite"):
        cmd_init(_args(tmp_path, production=False, force=False))
    cmd_schema(_args(tmp_path))
    assert all((tmp_path / "schemas" / name).exists() for name in ("project.schema.json", "manifest.schema.json", "fragment.schema.json"))
    assert json.loads((project / "app.json").read_text())["$schema"] == "../../schemas/manifest.schema.json"


def test_doctor_production_surfaces_operational_risks_and_route_shadow(monkeypatch):
    import framework.doctor as doctor

    project = ProjectConfig.model_validate(
        {
            "slug": "p",
            "name": "P",
            "docs_enabled": True,
            "databases": {"primary": {"url": "sqlite+aiosqlite:///x.db", "support_schema_mode": "create"}},
            "security": {
                "bootstrap_enabled": True,
                "bootstrap_admin_key": "change-me-" + "x" * 40,
                "jwt_enabled": True,
                "jwt_secret": "secret-" + "x" * 40,
                "require_https": False,
                "allow_websocket_query_api_key": True,
            },
            "cors_origins": ["*"],
            "protection": {"trusted_hosts": ["*"]},
            "rate_limit": {"backend": "memory"},
            "resources": [{"table": "items", "path": "items", "allowed_actions": ["read"], "permissions": {"read": "items.read"}}],
            "custom_endpoints": [{"path": "items/special", "method": "GET", "public": True, "handler": "x:y"}],
            "operations": [{"name": "write", "permission": "write", "statements": [{"sql": "UPDATE t SET x=1", "mode": "execute"}]}],
            "data_sources": [
                {
                    "name": "external",
                    "type": "http",
                    "url": "http://127.0.0.1/data",
                    "public": True,
                    "allow_insecure_http": True,
                    "allow_private_networks": True,
                }
            ],
            "event_channels": [{"name": "events", "publish_permission": "e.pub", "subscribe_permission": "e.sub"}],
            "media": {"enabled": True, "backend": "local"},
        }
    )
    monkeypatch.setattr(doctor.settings, "operator_token", "")
    monkeypatch.setattr(doctor.settings, "redis_url", None)
    codes = {d.code for d in project_diagnostics(project, production=True)}
    expected = {
        "route-shadow",
        "weak-bootstrap-secret",
        "weak-jwt-secret",
        "https-not-required",
        "operator-token-missing",
        "trusted-hosts-wildcard",
        "cors-wildcard",
        "public-project-docs",
        "memory-rate-limit",
        "memory-realtime",
        "local-media-storage",
        "sqlite-production",
        "runtime-schema-ddl",
        "mutation-without-cache-invalidation",
        "insecure-http-egress",
        "private-network-egress",
        "websocket-query-api-key",
    }
    assert expected.issubset(codes)
    assert route_shadows("/x/{id}", "/x/special") and not route_shadows("/x/static", "/x/special")
