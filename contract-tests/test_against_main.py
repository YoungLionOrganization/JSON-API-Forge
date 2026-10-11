from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from framework.editor_api import register_editor_api
from framework.editor_identity import init_editor_identity
from framework.settings import Settings
from sqlalchemy.ext.asyncio import create_async_engine

from json_api_forge import AsyncEditorControlPlaneClient

SETUP_TOKEN = "ForgeSetup_7wJ4fE9pQ2mR8yU6nC1xL5vA0sD3kH"


def _real_main_app(tmp_path: Path) -> FastAPI:
    apps = tmp_path / "app"
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

    settings = Settings(
        _env_file=None,
        app_env="development",
        editor_api_enabled=True,
        editor_token=SETUP_TOKEN,
        editor_require_https=False,
        editor_allowed_ips="",
        editor_trusted_hosts="localhost",
        editor_allow_create_projects=True,
        editor_allow_hooks=True,
        editor_allow_graphs=True,
        editor_attachment_dir=tmp_path / "attachments",
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'internal.db'}")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await init_editor_identity(engine, mode="create")
        app.state.internal_engine = engine
        yield
        await engine.dispose()

    app = FastAPI(lifespan=lifespan)
    register_editor_api(app, apps_dir=apps, settings=settings)
    return app


def test_python_sdk_against_main_control_plane(tmp_path: Path) -> None:
    async def run() -> None:
        app = _real_main_app(tmp_path)
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 43000))
        async with app.router.lifespan_context(app):
            async with AsyncEditorControlPlaneClient(
                "http://localhost",
                allow_insecure_http=True,
                transport=transport,
            ) as control:
                setup = await control.setup_founder(
                    SETUP_TOKEN,
                    "sdk-founder",
                    "Granite river orbits seven moons 42!",
                    "SDK Founder",
                )
                assert setup.data["profile"]["is_founder"] is True
                capabilities = (await control.capabilities()).data
                assert "roles.manage" in capabilities["permission_catalog"]
                assert capabilities["call_client_revision"] == 2
                area = await control.create_area({"project": "Notes", "name": "SDK project area", "visibility": "open"})
                assert (await control.post_message(area.data["id"], "Control-plane integration is online")).status_code == 201
                source = tmp_path / "Çağrı dosyası.txt"
                source.write_text("Unicode içerik: Azərbaycan — Türkiye", encoding="utf-8")
                uploaded = await control.upload_attachment(area.data["id"], source)
                assert (await control.attachments(area.data["id"])).data["attachments"][0]["id"] == uploaded.data["id"]
                target = tmp_path / "real-download.txt"
                await control.download_attachment(uploaded.data["id"], target)
                assert target.read_text(encoding="utf-8") == "Unicode içerik: Azərbaycan — Türkiye"
                call = await control.start_call(area.data["id"], mode="audio")
                ticket = await control.call_ticket(call.data["id"])
                url = control.call_client_url(ticket.data["call_client_path"], ticket.data["ticket"])
                assert "#ticket=" in url and "?ticket=" not in url
                await control.logout()
                assert not control.has_session

    asyncio.run(run())
