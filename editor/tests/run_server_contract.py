"""Run the native Editor against the canonical server using disposable local state."""
from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import os


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-source", type=Path, required=True)
    parser.add_argument("--test-binary", type=Path, required=True)
    args = parser.parse_args()
    server_source = args.server_source.resolve(strict=True)
    binary = args.test_binary.resolve(strict=True)
    sys.path.insert(0, str(server_source))

    from fastapi import FastAPI
    from sqlalchemy import Column, Integer, MetaData, String, Table, insert
    from sqlalchemy.ext.asyncio import create_async_engine
    import uvicorn
    from framework.settings import Settings
    from framework.editor_api import register_editor_api
    from framework.editor_identity import init_editor_identity

    with tempfile.TemporaryDirectory(prefix="forge-editor-contract-") as temporary:
        state = Path(temporary)
        project = state / "app" / "Workspace"
        (project / "config").mkdir(parents=True)
        (project / "app.json").write_text(json.dumps({
            "slug": "workspace", "name": "Workspace", "version": "1.0.0",
            "databases": {"primary": {"url": "sqlite+aiosqlite:///./data/workspace.db"}},
            "resources": [],
        }), encoding="utf-8")
        (project / "config" / "40-resources.json").write_text('{"resources":[]}\n', encoding="utf-8")
        settings = Settings(
            _env_file=None,
            app_env="development", editor_api_enabled=True,
            editor_token="forge-ui-test-setup-0123456789abcdefghijklmnopqrstuv",
            editor_require_https=False, editor_allowed_ips="127.0.0.1/32",
            editor_trusted_hosts="127.0.0.1,localhost",
            editor_allow_create_projects=True, editor_allow_graphs=True,
            editor_collaboration_enabled=True, editor_calls_enabled=True,
            editor_database_browser_enabled=True,
            editor_attachment_dir=state / "attachments",
        )
        identity_engine = create_async_engine(f"sqlite+aiosqlite:///{state / 'identity.db'}")
        data_engine = create_async_engine(f"sqlite+aiosqlite:///{state / 'project.db'}")
        metadata = MetaData()
        table = Table("items", metadata, Column("id", Integer, primary_key=True),
                      Column("name", String(80)), Column("secret", String(80)))
        resource = SimpleNamespace(enabled=True, database="primary", table="items", path="items",
                                   readable_fields=None, hidden_fields=["secret"], primary_key="id")
        runtime = SimpleNamespace(config=SimpleNamespace(slug="workspace", resources=[resource]),
                                  registry=SimpleNamespace(engines={"primary": data_engine},
                                                           tables={("primary", "items"): table}))

        @asynccontextmanager
        async def lifespan(app):
            await init_editor_identity(identity_engine, mode="create")
            async with data_engine.begin() as connection:
                await connection.run_sync(metadata.create_all)
                await connection.execute(insert(table).values(id=1, name="Native contract", secret="hidden"))
            app.state.internal_engine = identity_engine
            try:
                yield
            finally:
                await identity_engine.dispose()
                await data_engine.dispose()

        app = FastAPI(lifespan=lifespan)
        register_editor_api(app, apps_dir=state / "app", settings=settings, runtimes={"workspace": runtime})
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            listener.listen(128)
            server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
            thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
            thread.start()
            deadline = time.monotonic() + 15
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.05)
            if not server.started:
                raise RuntimeError("Canonical Editor API fixture did not start")
            env = {**os.environ, "QT_QPA_PLATFORM": "offscreen",
                   "FORGE_EDITOR_TEST_SERVER_URL": f"http://127.0.0.1:{port}"}
            try:
                result = subprocess.run([str(binary), "liveServerContract", "-o", "-,txt", "-o",
                                         str(binary.parent / "editor-contract-tests.xml") + ",junitxml"],
                                        env=env, timeout=120)
            finally:
                server.should_exit = True
                thread.join(timeout=10)
            if thread.is_alive():
                raise RuntimeError("Canonical Editor API fixture did not stop")
            return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
