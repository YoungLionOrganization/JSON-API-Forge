from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Column, Integer, MetaData, String, Table, insert
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.websockets import WebSocketDisconnect

from framework.editor_api import EditorControlPlane, register_editor_api
from framework.editor_call_client import parse_ice_servers
from framework.editor_database import browse_rows, database_catalog
from framework.editor_identity import EditorAccess, EditorPrincipal, _role_within_authority, init_editor_identity
from framework.settings import Settings

SETUP_TOKEN = "ForgeSetup_7wJ4fE9pQ2mR8yU6nC1xL5vA0sD3kH"


def _project(apps: Path) -> Path:
    project = apps / "Notes"
    (project / "config").mkdir(parents=True)
    (project / "hooks").mkdir()
    (project / "graphs").mkdir()
    (project / "app.json").write_text(
        json.dumps(
            {
                "slug": "notes",
                "name": "Notes",
                "databases": {"primary": {"url": "sqlite+aiosqlite:///./data/notes.db"}},
                "resources": [],
            }
        ),
        encoding="utf-8",
    )
    (project / "config" / "40-resources.json").write_text('{"resources": []}\n', encoding="utf-8")
    (project / "hooks" / "private.py").write_text("def run():\n    return True\n", encoding="utf-8")
    return project


def _settings(tmp_path: Path, **overrides) -> Settings:
    values = dict(
        _env_file=None,
        editor_api_enabled=True,
        editor_token=SETUP_TOKEN,
        editor_require_https=False,
        editor_allowed_ips="",
        editor_trusted_hosts="testserver,localhost",
        editor_allow_create_projects=True,
        editor_allow_hooks=True,
        editor_allow_graphs=True,
        editor_attachment_dir=tmp_path / "attachments",
        editor_login_max_attempts=3,
        editor_login_lock_seconds=60,
        editor_call_ice_servers_json=('[{"urls":"turns:turn.example.test:5349","username":"worker","credential":"TURN_TEST_SECRET"}]'),
    )
    values.update(overrides)
    return Settings(**values)


def _account_app(tmp_path: Path) -> tuple[FastAPI, Settings]:
    apps = tmp_path / "app"
    _project(apps)
    settings = _settings(tmp_path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'internal.db'}")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await init_editor_identity(engine, mode="create")
        app.state.internal_engine = engine
        yield
        await engine.dispose()

    app = FastAPI(lifespan=lifespan)
    register_editor_api(app, apps_dir=apps, settings=settings)
    return app, settings


def _founder(client: TestClient) -> str:
    response = client.post(
        "/__forge/editor/v1/setup/founder",
        headers={"X-Forge-Setup-Token": SETUP_TOKEN},
        json={"username": "founder", "password": "Granite river orbits seven moons 42!", "display_name": "Forge Founder"},
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


def test_call_ice_server_configuration_is_bounded() -> None:
    assert (
        parse_ice_servers('[{"urls":"turns:turn.example.test:5349","username":"worker","credential":"secret"}]')[0]["username"] == "worker"
    )
    with pytest.raises(RuntimeError):
        parse_ice_servers('[{"urls":"https://example.test/credential-leak"}]')
    with pytest.raises(RuntimeError):
        parse_ice_servers("[]")


def test_account_bootstrap_roles_invitations_and_project_scopes(tmp_path: Path) -> None:
    app, _ = _account_app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/__forge/editor/v1/setup/status").json()["initialized"] is False
        founder_token = _founder(client)
        founder_headers = {"Authorization": f"Bearer {founder_token}"}
        assert client.get("/__forge/editor/v1/setup/status").json()["initialized"] is True
        assert (
            client.post(
                "/__forge/editor/v1/setup/founder",
                headers={"X-Forge-Setup-Token": SETUP_TOKEN},
                json={"username": "other", "password": "Another difficult password 42!", "display_name": "Other"},
            ).status_code
            == 409
        )

        capabilities = client.get("/__forge/editor/v1/capabilities", headers=founder_headers).json()
        assert capabilities["api_version"] == 2
        assert capabilities["database_browser"] and capabilities["collaboration"] and capabilities["calls"]
        assert capabilities["cross_process_locking"] is True
        assert "roles.manage" in capabilities["permission_catalog"]

        invitation = client.post(
            "/__forge/editor/v1/invitations",
            headers=founder_headers,
            json={
                "memberships": [{"role_id": "00000000-0000-0000-0000-000000000003", "project": "Notes"}],
                "expires_hours": 24,
            },
        )
        assert invitation.status_code == 201
        worker = client.post(
            "/__forge/editor/v1/auth/register",
            json={
                "invitation": invitation.json()["invitation"],
                "username": "worker.one",
                "password": "Copper falcon maps quiet valleys 84!",
                "display_name": "Worker One",
            },
        )
        assert worker.status_code == 201, worker.text
        worker_headers = {"Authorization": f"Bearer {worker.json()['access_token']}"}
        assert client.get("/__forge/editor/v1/roles", headers=worker_headers).status_code == 403
        assert [item["directory"] for item in client.get("/__forge/editor/v1/projects", headers=worker_headers).json()["projects"]] == [
            "Notes"
        ]

        documents = client.get("/__forge/editor/v1/projects/Notes/documents", headers=worker_headers).json()["documents"]
        assert {item["path"] for item in documents} == {"app.json", "config/40-resources.json"}
        current = (tmp_path / "app/Notes/config/40-resources.json").read_bytes()
        saved = client.put(
            "/__forge/editor/v1/projects/Notes/documents/config/40-resources.json",
            headers=worker_headers,
            json={"content": '{\n  "resources": []\n}\n', "expected_sha256": hashlib.sha256(current).hexdigest()},
        )
        assert saved.status_code == 200
        assert (
            client.put(
                "/__forge/editor/v1/projects/Notes/documents/hooks/private.py",
                headers=worker_headers,
                json={"content": "print('unsafe')\n", "expected_sha256": "new"},
            ).status_code
            == 403
        )
        members = client.get("/__forge/editor/v1/members", headers=founder_headers).json()["members"]
        assert {member["username"] for member in members} == {"founder", "worker.one"}


def test_login_throttle_profile_collaboration_files_and_calls(tmp_path: Path) -> None:
    app, settings = _account_app(tmp_path)
    with TestClient(app) as client:
        token = _founder(client)
        headers = {"Authorization": f"Bearer {token}"}
        for _ in range(settings.editor_login_max_attempts):
            response = client.post("/__forge/editor/v1/auth/login", json={"username": "founder", "password": "wrong password"})
            assert response.status_code == 401
        locked = client.post(
            "/__forge/editor/v1/auth/login",
            json={"username": "founder", "password": "Granite river orbits seven moons 42!"},
        )
        assert locked.status_code == 429 and int(locked.headers["Retry-After"]) > 0

        profile = client.patch(
            "/__forge/editor/v1/me",
            headers=headers,
            json={"title": "Platform founder", "bio": "Maintains the Forge control plane.", "timezone": "Asia/Baku"},
        )
        assert profile.status_code == 200 and profile.json()["timezone"] == "Asia/Baku"

        area = client.post(
            "/__forge/editor/v1/areas",
            headers=headers,
            json={"project": "Notes", "name": "General project area", "visibility": "open"},
        )
        assert area.status_code == 201
        area_id = area.json()["id"]
        message = client.post(
            f"/__forge/editor/v1/areas/{area_id}/messages",
            headers=headers,
            json={"body": "The migration window starts at 21:00.", "kind": "announcement"},
        )
        assert message.status_code == 201
        assert client.get(f"/__forge/editor/v1/areas/{area_id}/messages", headers=headers).json()["messages"][0]["kind"] == "announcement"

        note = client.post(
            "/__forge/editor/v1/notes",
            headers=headers,
            json={"project": "Notes", "area_id": area_id, "title": "Migration checklist", "body": "Back up, validate, deploy."},
        )
        assert note.status_code == 201
        assert client.get("/__forge/editor/v1/notes?project=Notes", headers=headers).json()["notes"][0]["revision"] == 1

        uploaded = client.post(
            f"/__forge/editor/v1/areas/{area_id}/attachments",
            headers=headers,
            files={"upload": ("plan.txt", b"signed migration plan", "text/plain")},
        )
        assert uploaded.status_code == 201, uploaded.text
        attachment_id = uploaded.json()["id"]
        attachments = client.get(f"/__forge/editor/v1/areas/{area_id}/attachments", headers=headers)
        assert attachments.status_code == 200
        listed = attachments.json()["attachments"]
        assert listed[0]["id"] == attachment_id
        assert listed[0]["original_name"] == "plan.txt"
        assert "stored_name" not in listed[0]
        downloaded = client.get(f"/__forge/editor/v1/attachments/{attachment_id}", headers=headers)
        assert downloaded.content == b"signed migration plan"
        assert "attachment" in downloaded.headers["content-disposition"].lower()

        call = client.post("/__forge/editor/v1/calls", headers=headers, json={"area_id": area_id, "mode": "video"})
        assert call.status_code == 201
        call_id = call.json()["id"]
        ticket = client.post(f"/__forge/editor/v1/calls/{call_id}/ticket", headers=headers).json()["ticket"]
        page = client.get(f"/__forge/editor/v1/call-client/{call_id}")
        assert page.status_code == 200
        assert ticket not in page.text
        assert "TURN_TEST_SECRET" not in page.text
        assert "script-src 'nonce-" in page.headers["content-security-policy"]
        assert "camera=(self)" in page.headers["permissions-policy"]
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/__forge/editor/v1/ws/calls/{call_id}?ticket={ticket}") as socket:
                socket.receive_json()
        with client.websocket_connect(f"/__forge/editor/v1/ws/calls/{call_id}", subprotocols=["forge-call-v1", ticket]) as socket:
            assert socket.accepted_subprotocol == "forge-call-v1"
            hello = socket.receive_json()
            assert hello["type"] == "peers" and hello["peers"] == []
            assert hello["mode"] == "video"
            assert hello["ice_servers"][0]["credential"] == "TURN_TEST_SECRET"
            socket.send_json({"type": "heartbeat"})
            socket.send_json({"type": "hangup"})
            with pytest.raises(WebSocketDisconnect):
                socket.receive_json()
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/__forge/editor/v1/ws/calls/{call_id}", subprotocols=["forge-call-v1", ticket]) as socket:
                socket.receive_json()


@pytest.mark.asyncio
async def test_database_browser_is_read_only_and_respects_hidden_fields(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'project.db'}")
    metadata = MetaData()
    table = Table(
        "customers",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String(80)),
        Column("secret", String(80)),
    )
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
        await connection.execute(insert(table).values(id=1, name="Ada", secret="not-for-editor"))
    resource = SimpleNamespace(
        enabled=True,
        database="primary",
        table="customers",
        path="customers",
        readable_fields=None,
        hidden_fields=["secret"],
        primary_key="id",
    )
    runtime = SimpleNamespace(
        config=SimpleNamespace(slug="crm", resources=[resource]),
        registry=SimpleNamespace(engines={"primary": engine}, tables={("primary", "customers"): table}),
    )
    principal = EditorPrincipal("u1", "founder", "Founder", True, None)
    access = EditorAccess(principal, frozenset({"*"}), frozenset(), frozenset({"Founder"}), 1000, ("*",), (), ("*",))
    catalog = database_catalog(runtime, access, expose_undeclared=False)
    assert catalog["raw_sql"] is False
    assert catalog["databases"][0]["tables"][0]["columns"][2]["readable"] is False
    rows = await browse_rows(runtime, access, alias="primary", table_name="customers", limit=10, offset=0, expose_undeclared=False)
    assert rows["rows"] == [{"id": 1, "name": "Ada"}]
    assert rows["read_only"] is True
    await engine.dispose()


def test_editor_rejects_symlinked_control_documents(tmp_path: Path) -> None:
    apps = tmp_path / "app"
    project = _project(apps)
    outside = tmp_path / "outside.json"
    outside.write_text('{"secret": true}', encoding="utf-8")
    (project / "config" / "90-secret.json").symlink_to(outside)
    control = EditorControlPlane(apps, _settings(tmp_path))
    with pytest.raises(Exception, match="symlinked"):
        control.list_documents("Notes")


def test_editor_can_start_after_setup_secret_is_removed(tmp_path: Path) -> None:
    app = FastAPI()
    register_editor_api(
        app,
        apps_dir=tmp_path,
        settings=_settings(
            tmp_path,
            editor_token="",
            editor_setup_enabled=False,
            editor_legacy_token_enabled=False,
        ),
    )
    assert "/__forge/editor/v1/auth/login" in app.openapi()["paths"]


def test_delegated_role_scope_cannot_escape_with_glob_characters() -> None:
    principal = EditorPrincipal("worker", "worker", "Worker", False, None)
    caller = EditorAccess(
        principal,
        frozenset({"projects.read", "documents.read", "databases.rows.read"}),
        frozenset({"manager"}),
        frozenset({"Manager"}),
        500,
        ("config/?",),
        (),
        ("primary:?",),
    )
    literal_child = {
        "id": "literal",
        "rank": 100,
        "permissions": ["projects.read"],
        "document_allow": ["config/a"],
        "document_deny": [],
        "database_allow": ["primary:x"],
    }
    wildcard_child = {
        **literal_child,
        "id": "wildcard",
        "document_allow": ["config/*"],
        "database_allow": ["primary:*"],
    }
    assert _role_within_authority(literal_child, caller)
    assert not _role_within_authority(wildcard_child, caller)

    denied_caller = EditorAccess(
        principal,
        caller.permissions,
        caller.role_ids,
        caller.role_names,
        caller.rank,
        ("config/*",),
        ("config/secret.json",),
        caller.database_allow,
    )
    role_without_inherited_deny = {**literal_child, "document_allow": ["config/a"]}
    assert not _role_within_authority(role_without_inherited_deny, denied_caller)
    role_with_inherited_deny = {
        **role_without_inherited_deny,
        "document_deny": ["config/secret.json"],
    }
    assert _role_within_authority(role_with_inherited_deny, denied_caller)


def test_invalid_invitation_is_rejected_before_password_hashing(tmp_path: Path, monkeypatch) -> None:
    app, _ = _account_app(tmp_path)

    def must_not_run(_password: str) -> str:
        raise AssertionError("password KDF ran for an invalid invitation")

    monkeypatch.setattr("framework.editor_identity._password_digest", must_not_run)
    with TestClient(app) as client:
        response = client.post(
            "/__forge/editor/v1/auth/register",
            json={
                "invitation": "jfi_" + "!" * 40,
                "username": "worker.two",
                "password": "Copper falcon maps quiet valleys 84!",
                "display_name": "Worker Two",
            },
        )
    assert response.status_code == 401


def test_role_mutation_revokes_pending_invitation(tmp_path: Path) -> None:
    app, _ = _account_app(tmp_path)
    with TestClient(app) as client:
        founder_headers = {"Authorization": f"Bearer {_founder(client)}"}
        role_payload = {
            "name": "Release Helper",
            "rank": 300,
            "permissions": ["projects.read", "documents.read"],
            "document_allow": ["app.json"],
            "document_deny": [],
            "database_allow": [],
        }
        role = client.post("/__forge/editor/v1/roles", headers=founder_headers, json=role_payload)
        assert role.status_code == 201, role.text
        role_id = role.json()["id"]
        invitation = client.post(
            "/__forge/editor/v1/invitations",
            headers=founder_headers,
            json={"memberships": [{"role_id": role_id, "project": "Notes"}]},
        )
        assert invitation.status_code == 201
        role_payload["permissions"] = ["projects.read"]
        assert client.put(f"/__forge/editor/v1/roles/{role_id}", headers=founder_headers, json=role_payload).status_code == 200
        rejected = client.post(
            "/__forge/editor/v1/auth/register",
            json={
                "invitation": invitation.json()["invitation"],
                "username": "worker.three",
                "password": "Copper falcon maps quiet valleys 84!",
                "display_name": "Worker Three",
            },
        )
        assert rejected.status_code == 401
