from __future__ import annotations

import asyncio
import io
import threading
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from json_api_forge import AsyncEditorControlPlaneClient, AsyncForgeClient, control_plane
from json_api_forge._io import run_io

SESSION = "jfe_session_" + "A" * 48


@pytest.mark.asyncio
async def test_attachment_snapshot_save_and_close_run_outside_event_loop(tmp_path: Path, monkeypatch) -> None:
    loop_thread = threading.get_ident()
    source = tmp_path / "source.txt"
    source.write_bytes(b"upload data")
    original_open = control_plane._open_attachment
    original_save = control_plane._atomic_save
    seen = []

    @contextmanager
    def checked_open(*args):
        assert threading.get_ident() != loop_thread
        with original_open(*args) as upload:
            seen.append("snapshot")
            yield upload
        assert threading.get_ident() != loop_thread
        seen.append("closed")

    def checked_save(*args):
        assert threading.get_ident() != loop_thread
        seen.append("save")
        return original_save(*args)

    monkeypatch.setattr(control_plane, "_open_attachment", checked_open)
    monkeypatch.setattr(control_plane, "_atomic_save", checked_save)

    async def handler(request):
        if request.method == "POST":
            assert b"upload data" in await request.aread()
            return httpx.Response(201, json={"id": "one"})
        return httpx.Response(200, content=b"download data")

    async with AsyncEditorControlPlaneClient("https://forge.test", transport=httpx.MockTransport(handler)) as client:
        client.set_session_token(SESSION)
        await client.upload_attachment("area", source)
        saved = await client.download_attachment("one", tmp_path / "saved.txt")
        assert saved.data.read_bytes() == b"download data"
    assert seen == ["snapshot", "closed", "save"]


@pytest.mark.asyncio
async def test_async_multipart_seek_and_read_run_outside_event_loop() -> None:
    loop_thread = threading.get_ident()
    reads = []

    class CheckedFile(io.BytesIO):
        def seek(self, *args):
            assert threading.get_ident() != loop_thread
            return super().seek(*args)

        def read(self, *args):
            assert threading.get_ident() != loop_thread
            reads.append(True)
            return super().read(*args)

    async def handler(request):
        assert b"multipart bytes" in await request.aread()
        return httpx.Response(200, json={"ok": True})

    handle = CheckedFile(b"multipart bytes")
    async with AsyncForgeClient("https://forge.test", transport=httpx.MockTransport(handler)) as client:
        assert (await client.request("POST", "upload", files={"upload": ("file.txt", handle)})).data["ok"]
    assert reads
    assert not handle.closed  # Generic caller-owned files remain caller-owned.
    handle.close()


@pytest.mark.asyncio
async def test_cancelled_snapshot_drains_before_closing_owned_file(tmp_path: Path, monkeypatch) -> None:
    started = threading.Event()
    release = threading.Event()
    closed = threading.Event()
    handle = io.BytesIO(b"snapshot")

    @contextmanager
    def slow_open(*_args):
        started.set()
        assert release.wait(5)
        try:
            yield "snapshot.txt", handle
        finally:
            handle.close()
            closed.set()

    monkeypatch.setattr(control_plane, "_open_attachment", slow_open)
    async with AsyncEditorControlPlaneClient("https://forge.test") as client:
        client.set_session_token(SESSION)
        task = asyncio.create_task(client.upload_attachment("area", tmp_path / "unused"))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            assert not closed.is_set()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert closed.is_set() and handle.closed


@pytest.mark.asyncio
async def test_cancelled_io_preserves_cancellation_if_worker_fails() -> None:
    started = threading.Event()
    release = threading.Event()

    def failure():
        started.set()
        assert release.wait(5)
        raise OSError("disk failed")

    task = asyncio.create_task(run_io(failure))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_io_failure_is_reported_normally() -> None:
    def failure():
        raise OSError("disk failed")

    with pytest.raises(OSError, match="disk failed"):
        await run_io(failure)


@pytest.mark.asyncio
async def test_cancelled_multipart_drains_read_before_snapshot_closes(tmp_path: Path, monkeypatch) -> None:
    reading = threading.Event()
    release = threading.Event()
    closed = threading.Event()

    class SlowFile(io.BytesIO):
        def read(self, *args):
            reading.set()
            assert release.wait(5)
            assert not self.closed
            return super().read(*args)

    handle = SlowFile(b"slow file")

    @contextmanager
    def snapshot(*_args):
        try:
            yield "snapshot.bin", handle
        finally:
            handle.close()
            closed.set()

    class ReadingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            await request.aread()
            return httpx.Response(201, json={"id": "one"})

    monkeypatch.setattr(control_plane, "_open_attachment", snapshot)
    async with AsyncEditorControlPlaneClient("https://forge.test", transport=ReadingTransport()) as client:
        client.set_session_token(SESSION)
        task = asyncio.create_task(client.upload_attachment("area", tmp_path / "unused"))
        try:
            assert await asyncio.to_thread(reading.wait, 2)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and not closed.is_set()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert closed.is_set()


@pytest.mark.asyncio
async def test_cancelled_download_finishes_atomic_save_before_returning(tmp_path: Path, monkeypatch) -> None:
    saving = threading.Event()
    release = threading.Event()
    original_save = control_plane._atomic_save
    target = tmp_path / "saved.bin"
    target.write_bytes(b"old")

    def slow_save(*args):
        saving.set()
        assert release.wait(5)
        return original_save(*args)

    monkeypatch.setattr(control_plane, "_atomic_save", slow_save)
    async with AsyncEditorControlPlaneClient(
        "https://forge.test", transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"new"))
    ) as client:
        client.set_session_token(SESSION)
        task = asyncio.create_task(client.download_attachment("one", target))
        try:
            assert await asyncio.to_thread(saving.wait, 2)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            assert target.read_bytes() == b"old"
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert target.read_bytes() == b"new"
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.asyncio
async def test_failed_snapshot_is_reported_without_attempting_upload(tmp_path: Path) -> None:
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={})

    async with AsyncEditorControlPlaneClient("https://forge.test", transport=httpx.MockTransport(handler)) as client:
        client.set_session_token(SESSION)
        with pytest.raises(ValueError, match="unavailable"):
            await client.upload_attachment("area", tmp_path / "missing")
    assert not requests
