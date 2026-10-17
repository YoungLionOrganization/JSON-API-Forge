import asyncio
import io
import threading
import time

import pytest
from fastapi import UploadFile

from framework import media
from framework.config import MediaConfig


@pytest.mark.asyncio
async def test_slow_media_flush_does_not_block_other_requests(tmp_path, monkeypatch):
    store = media.LocalMediaStore(str(tmp_path))
    real_fsync = media.os.fsync
    loop_thread = threading.get_ident()
    flush_threads = []
    heartbeats = 0

    def slow_fsync(fd):
        flush_threads.append(threading.get_ident())
        time.sleep(0.12)
        real_fsync(fd)

    async def heartbeat():
        nonlocal heartbeats
        while True:
            heartbeats += 1
            await asyncio.sleep(0.005)

    monkeypatch.setattr(media.os, "fsync", slow_fsync)
    pulse = asyncio.create_task(heartbeat())
    upload = UploadFile(io.BytesIO(b"payload"), filename="a.bin")
    try:
        size, digest = await store.save_upload(upload, "app/a.bin", 100)
    finally:
        pulse.cancel()
        await asyncio.gather(pulse, return_exceptions=True)
    assert size == 7 and len(digest) == 64
    assert heartbeats >= 3
    assert flush_threads and all(thread != loop_thread for thread in flush_threads)
    assert upload.file.closed
    assert (tmp_path / "app/a.bin").read_bytes() == b"payload"


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["flush", "publish", "read"])
async def test_cancelled_upload_drains_io_and_removes_owned_files(tmp_path, monkeypatch, stage):
    store = media.LocalMediaStore(str(tmp_path))
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    real_fsync = media.os.fsync
    real_link = media.os.link
    calls = 0

    def wait_for_cancel():
        started.set()
        assert release.wait(3), "test did not release IO worker"
        finished.set()

    def slow_fsync(fd):
        nonlocal calls
        calls += 1
        if stage == "flush" and calls == 1:
            wait_for_cancel()
        return real_fsync(fd)

    def slow_link(*args, **kwargs):
        result = real_link(*args, **kwargs)
        if stage == "publish":
            wait_for_cancel()
        return result

    class DelayedRead(UploadFile):
        async def read(self, size=-1):
            if stage == "read":
                started.set()
                await asyncio.Event().wait()
            return await super().read(size)

    monkeypatch.setattr(media.os, "fsync", slow_fsync)
    monkeypatch.setattr(media.os, "link", slow_link)
    upload = DelayedRead(io.BytesIO(b"payload"), filename="a.bin")
    task = asyncio.create_task(store.save_upload(upload, "app/a.bin", 100))

    async def wait_started():
        while not started.is_set():
            await asyncio.sleep(0.005)

    try:
        await asyncio.wait_for(wait_started(), timeout=4)
        task.cancel()
        await asyncio.sleep(0.01)
        task.cancel()
        if stage != "read":
            assert not task.done(), "upload returned while its IO worker was still running"
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert stage == "read" or finished.is_set()
    assert upload.file.closed
    assert not (tmp_path / "app/a.bin").exists()
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.asyncio
async def test_publication_durability_failure_removes_new_object(tmp_path, monkeypatch):
    if media.os.name != "posix":
        pytest.skip("directory descriptor fsync is a POSIX durability step")
    real_fsync = media.os.fsync
    calls = 0

    def fail_directory_flush(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("directory flush failed")
        return real_fsync(fd)

    monkeypatch.setattr(media.os, "fsync", fail_directory_flush)
    upload = UploadFile(io.BytesIO(b"payload"))
    store = media.LocalMediaStore(str(tmp_path))
    with pytest.raises(OSError, match="directory flush failed"):
        await store.save_upload(upload, "app/a.bin", 100)
    assert upload.file.closed
    assert not (tmp_path / "app/a.bin").exists()
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_media_output_close_runs_off_event_loop_even_on_write_failure(tmp_path, monkeypatch, failure):
    native_fdopen = media.os.fdopen
    loop_thread = threading.get_ident()
    close_threads = []

    class Output:
        def __init__(self, *args, **kwargs):
            self.file = native_fdopen(*args, **kwargs)

        def write(self, chunk):
            if failure:
                raise OSError("disk write failed")
            return self.file.write(chunk)

        def flush(self):
            return self.file.flush()

        def fileno(self):
            return self.file.fileno()

        def close(self):
            close_threads.append(threading.get_ident())
            time.sleep(0.03)
            self.file.close()

    monkeypatch.setattr(media.os, "fdopen", Output)
    upload = UploadFile(io.BytesIO(b"payload"))
    store = media.LocalMediaStore(str(tmp_path))
    if failure:
        with pytest.raises(OSError, match="disk write failed"):
            await store.save_upload(upload, "app/a.bin", 100)
        assert not (tmp_path / "app/a.bin").exists()
    else:
        await store.save_upload(upload, "app/a.bin", 100)
        assert (tmp_path / "app/a.bin").read_bytes() == b"payload"
    assert close_threads and all(thread != loop_thread for thread in close_threads)
    assert upload.file.closed
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.asyncio
async def test_repeated_cancellation_during_input_close_does_not_publish(tmp_path):
    closing = asyncio.Event()
    release = asyncio.Event()

    class SlowClose(UploadFile):
        async def close(self):
            closing.set()
            await release.wait()
            await super().close()

    store = media.LocalMediaStore(str(tmp_path))
    upload = SlowClose(io.BytesIO(b"payload"))
    task = asyncio.create_task(store.save_upload(upload, "app/a.bin", 100))
    try:
        await asyncio.wait_for(closing.wait(), timeout=3)
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
    assert upload.file.closed
    assert not (tmp_path / "app/a.bin").exists()
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_cancelled_media_registration_keeps_database_and_object_consistent(tmp_path, failure):
    from contextlib import asynccontextmanager

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'metadata.db'}")
    committed = asyncio.Event()
    release = asyncio.Event()

    class DelayedEngine:
        @asynccontextmanager
        async def begin(self):
            async with engine.begin() as connection:
                yield connection
                if failure:
                    committed.set()
                    await release.wait()
                    raise OSError("metadata commit failed")
            committed.set()
            await release.wait()

    async with engine.begin() as connection:
        await connection.run_sync(media.media_table.metadata.create_all)
    store = media.LocalMediaStore(str(tmp_path / "media"))
    config = MediaConfig(deduplicate=False, max_owner_bytes=None, allowed_mime_types=[], allowed_extensions=[])
    upload = UploadFile(io.BytesIO(b"payload"), filename="a.bin")
    task = asyncio.create_task(
        media.save_media(engine=DelayedEngine(), store=store, project_slug="app", config=config, upload=upload, owner_subject="owner")
    )
    try:
        await asyncio.wait_for(committed.wait(), timeout=4)
        task.cancel()
        await asyncio.sleep(0.01)
        task.cancel()
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with engine.connect() as connection:
            rows = (await connection.execute(select(media.media_table))).mappings().all()
        objects = [path for path in store.root.rglob("*") if path.is_file()]
        if failure:
            assert rows == [] and objects == []
        else:
            assert len(rows) == 1 and len(objects) == 1
            assert store.path_for(rows[0]["storage_key"]).read_bytes() == b"payload"
        assert upload.file.closed
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await engine.dispose()
