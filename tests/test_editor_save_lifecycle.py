from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from filelock import FileLock

import framework.editor_api as editor
from framework.editor_api import DocumentWrite, EditorControlPlane
from framework.settings import Settings


def control_for(tmp_path):
    project = tmp_path / "app/Notes"
    (project / "config").mkdir(parents=True)
    (project / "app.json").write_text(
        json.dumps({"slug": "notes", "name": "Notes", "databases": {"primary": {"url": "sqlite+aiosqlite:///:memory:"}}}),
        encoding="utf-8",
    )
    target = project / "config/resources.json"
    target.write_text('{"resources": []}\n', encoding="utf-8")
    control = EditorControlPlane(project.parent, Settings(_env_file=None))
    payload = DocumentWrite(content='{\n"resources": []\n}\n', expected_sha256=hashlib.sha256(target.read_bytes()).hexdigest())
    return control, project, target, payload


@pytest.mark.asyncio
async def test_save_locks_and_files_stay_in_the_same_worker(tmp_path, monkeypatch):
    control, _, target, payload = control_for(tmp_path)
    activity = []
    native = FileLock

    def lock_factory(*args, **kwargs):
        lock = native(*args, **kwargs)

        class TrackingLock:
            def acquire(self, *args, **kwargs):
                activity.append(("acquire", threading.get_ident()))
                return lock.acquire(*args, **kwargs)

            def release(self):
                activity.append(("release", threading.get_ident()))
                return lock.release()

        return TrackingLock()

    stage = control._stage_project

    def track_stage(*args):
        activity.append(("stage", threading.get_ident()))
        return stage(*args)

    monkeypatch.setattr(editor, "FileLock", lock_factory)
    monkeypatch.setattr(control, "_stage_project", track_stage)
    main_thread = threading.get_ident()
    digest = await control.write_document("Notes", "config/resources.json", payload)
    assert digest == hashlib.sha256(target.read_bytes()).hexdigest()
    assert [name for name, _ in activity] == ["acquire", "stage", "release"]
    assert len({thread for _, thread in activity}) == 1
    assert activity[0][1] != main_thread
    # A second real save must reacquire rather than inherit a leaked file lock.
    await control.write_document("Notes", "config/resources.json", DocumentWrite(content='{"resources": []}', expected_sha256=digest))


@pytest.mark.asyncio
async def test_slow_staging_keeps_event_loop_responsive_and_cancel_drains_worker(tmp_path, monkeypatch):
    control, _, target, payload = control_for(tmp_path)
    before = target.read_bytes()
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    stage = control._stage_project

    def slow_stage(*args):
        entered.set()
        try:
            if not release.wait(2):
                raise RuntimeError("Test stage was never released")
            return stage(*args)
        finally:
            finished.set()

    monkeypatch.setattr(control, "_stage_project", slow_stage)
    task = asyncio.create_task(control.write_document("Notes", "config/resources.json", payload))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        assert not finished.is_set(), "Staging blocked the loop until the worker finished"
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done() and control._write_lock.locked()
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done() and control._write_lock.locked()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set() and not control._write_lock.locked()
        assert target.read_bytes() == before
        assert not list(target.parent.glob(".*.tmp"))
        monkeypatch.setattr(control, "_stage_project", stage)
        await control.write_document("Notes", "config/resources.json", payload)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_lock_wait_does_not_acquire_a_lock_after_returning(tmp_path, monkeypatch):
    control, project, _, payload = control_for(tmp_path)
    control._lock_dir.mkdir()
    path = control._lock_dir / (hashlib.sha256(str(project).encode()).hexdigest() + ".lock")
    held = FileLock(path)
    held.acquire()
    attempted = threading.Event()
    native = editor.FileLock

    def lock_factory(*args, **kwargs):
        lock = native(*args, **kwargs)

        class TrackingLock:
            def acquire(self, *args, **kwargs):
                attempted.set()
                return lock.acquire(*args, **kwargs)

            def release(self):
                return lock.release()

        return TrackingLock()

    monkeypatch.setattr(editor, "FileLock", lock_factory)
    task = asyncio.create_task(control.write_document("Notes", "config/resources.json", payload))
    try:
        assert await asyncio.to_thread(attempted.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert not control._write_lock.locked()
        held.release()
        # The cancelled worker must be fully gone before this next save begins.
        await asyncio.wait_for(control.write_document("Notes", "config/resources.json", payload), 1)
    finally:
        held.release()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["stage", "replace"])
async def test_save_failure_releases_lock_and_preserves_existing_document(tmp_path, monkeypatch, phase):
    control, _, target, payload = control_for(tmp_path)
    before = target.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("simulated disk failure")

    with monkeypatch.context() as patch:
        if phase == "stage":
            patch.setattr(control, "_stage_project", fail)
        else:
            patch.setattr(editor.os, "replace", fail)
        with pytest.raises(OSError, match="disk failure"):
            await control.write_document("Notes", "config/resources.json", payload)
    assert target.read_bytes() == before
    assert not list(target.parent.glob(".*.tmp"))
    await asyncio.wait_for(control.write_document("Notes", "config/resources.json", payload), 1)


@pytest.mark.asyncio
async def test_project_busy_deadline_is_preserved(tmp_path, monkeypatch):
    control, project, _, payload = control_for(tmp_path)
    control._lock_dir.mkdir()
    path = control._lock_dir / (hashlib.sha256(str(project).encode()).hexdigest() + ".lock")
    held = FileLock(path)
    held.acquire()
    ticks = iter([0, 11])
    monkeypatch.setattr(editor, "time", SimpleNamespace(monotonic=lambda: next(ticks, 11)))
    try:
        # Direct worker invocation avoids altering the async loop's own clock.
        with pytest.raises(HTTPException) as exc:
            control._write_document_sync(project, target=None, normalized=None, data=None, payload=payload, cancelled=threading.Event())
        assert exc.value.status_code == 503 and exc.value.headers == {"Retry-After": "1"}
    finally:
        held.release()
