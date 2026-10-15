import asyncio
import threading
import time
from contextlib import asynccontextmanager

import httpx
import pytest

from framework import editor_api as editor
from tests.test_v050_editor_collaboration import SETUP_TOKEN, _account_app


@asynccontextmanager
async def connected_editor(tmp_path):
    app, settings = _account_app(tmp_path)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.post(
                "/__forge/editor/v1/setup/founder",
                headers={"X-Forge-Setup-Token": SETUP_TOKEN},
                json={"username": "founder", "password": "Granite river orbits seven moons 42!", "display_name": "Founder"},
            )
            assert response.status_code == 201, response.text
            client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
            response = await client.post("/__forge/editor/v1/areas", json={"project": "Notes", "name": "Attachments", "visibility": "open"})
            assert response.status_code == 201, response.text
            area = response.json()["id"]
            yield client, area, settings.editor_attachment_dir


@pytest.mark.asyncio
async def test_attachment_flush_and_output_close_do_not_block_requests(tmp_path, monkeypatch):
    real_fsync = editor.os.fsync
    real_fdopen = editor.os.fdopen
    loop_thread = threading.get_ident()
    disk_threads = []
    ticks = 0

    def slow_fsync(fd):
        disk_threads.append(threading.get_ident())
        time.sleep(0.08)
        real_fsync(fd)

    class Output:
        def __init__(self, *args, **kwargs):
            self.file = real_fdopen(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.file, name)

        def close(self):
            disk_threads.append(threading.get_ident())
            time.sleep(0.08)
            self.file.close()

    async def heartbeat():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.005)

    async with connected_editor(tmp_path) as (client, area, root):
        monkeypatch.setattr(editor.os, "fsync", slow_fsync)
        monkeypatch.setattr(editor.os, "fdopen", Output)
        pulse = asyncio.create_task(heartbeat())
        try:
            response = await client.post(f"/__forge/editor/v1/areas/{area}/attachments", files={"upload": ("file.txt", b"payload")})
        finally:
            pulse.cancel()
            await asyncio.gather(pulse, return_exceptions=True)
        assert response.status_code == 201, response.text
        assert ticks >= 3
        assert disk_threads and all(thread != loop_thread for thread in disk_threads)
        downloaded = await client.get("/__forge/editor/v1/attachments/" + response.json()["id"])
        assert downloaded.status_code == 200 and downloaded.content == b"payload"
        assert len(list(root.glob("*.blob"))) == 1
        assert not list(root.glob(".upload-*"))


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["flush", "publish", "close"])
async def test_cancelled_attachment_drains_disk_worker_before_cleanup(tmp_path, monkeypatch, stage):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    real_fsync = editor.os.fsync
    real_replace = editor.os.replace
    real_fdopen = editor.os.fdopen

    def wait_for_cancel():
        started.set()
        assert release.wait(3), "IO worker was not released"
        finished.set()

    def slow_fsync(fd):
        if stage == "flush":
            wait_for_cancel()
        real_fsync(fd)

    def slow_replace(*args, **kwargs):
        real_replace(*args, **kwargs)
        if stage == "publish":
            wait_for_cancel()

    class Output:
        def __init__(self, *args, **kwargs):
            self.file = real_fdopen(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.file, name)

        def close(self):
            if stage == "close":
                wait_for_cancel()
            self.file.close()

    async def wait_started():
        while not started.is_set():
            await asyncio.sleep(0.005)

    async with connected_editor(tmp_path) as (client, area, root):
        monkeypatch.setattr(editor.os, "fsync", slow_fsync)
        monkeypatch.setattr(editor.os, "replace", slow_replace)
        monkeypatch.setattr(editor.os, "fdopen", Output)
        task = asyncio.create_task(client.post(f"/__forge/editor/v1/areas/{area}/attachments", files={"upload": ("a.txt", b"payload")}))
        try:
            await asyncio.wait_for(wait_started(), timeout=4)
            task.cancel()
            await asyncio.sleep(0.01)
            task.cancel()
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        assert finished.is_set()
        assert not list(root.iterdir())
        response = await client.get(f"/__forge/editor/v1/areas/{area}/attachments")
        assert response.status_code == 200 and response.json()["attachments"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_cancelled_attachment_registration_preserves_only_committed_files(tmp_path, monkeypatch, failure):
    registered = asyncio.Event()
    release = asyncio.Event()
    real_register = editor.EditorIdentityStore.register_attachment

    async def delayed_register(self, *args, **kwargs):
        if failure:
            registered.set()
            await release.wait()
            raise OSError("metadata storage failed")
        result = await real_register(self, *args, **kwargs)
        registered.set()
        await release.wait()
        return result

    async with connected_editor(tmp_path) as (client, area, root):
        monkeypatch.setattr(editor.EditorIdentityStore, "register_attachment", delayed_register)
        task = asyncio.create_task(client.post(f"/__forge/editor/v1/areas/{area}/attachments", files={"upload": ("a.txt", b"payload")}))
        try:
            await asyncio.wait_for(registered.wait(), timeout=4)
            task.cancel()
            await asyncio.sleep(0.01)
            task.cancel()
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        response = await client.get(f"/__forge/editor/v1/areas/{area}/attachments")
        assert response.status_code == 200
        attachments = response.json()["attachments"]
        if failure:
            assert attachments == [] and not list(root.iterdir())
        else:
            assert len(attachments) == 1
            response = await client.get("/__forge/editor/v1/attachments/" + attachments[0]["id"])
            assert response.status_code == 200 and response.content == b"payload"
            assert len(list(root.glob("*.blob"))) == 1
        assert not list(root.glob(".upload-*"))
