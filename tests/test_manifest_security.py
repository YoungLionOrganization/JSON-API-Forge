from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("sdk_manifest_check", Path(__file__).parents[1] / "scripts/check_manifest.py")
manifest = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manifest)


@pytest.fixture
def manifest_root(tmp_path, monkeypatch):
    root = tmp_path / "release"
    root.mkdir()
    monkeypatch.setattr(manifest, "ROOT", root)
    monkeypatch.setattr(manifest, "MANIFEST", root / "MANIFEST.sha256")
    monkeypatch.setattr(manifest.subprocess, "check_output", lambda *_args, **_kwargs: root.as_posix())
    return root


@pytest.mark.parametrize(
    "relative,checksum",
    [
        ("../private.txt", "a" * 64),
        ("/absolute/private.txt", "a" * 64),
        ("C:/outside/private.txt", "a" * 64),
        ("scripts\\tool.py", "a" * 64),
        ("README.md:secret-stream", "a" * 64),
        (".env", "a" * 64),
        ("build/private.txt", "a" * 64),
        ("README.md", "not-a-sha256"),
        ("README.md", "A" * 64),
    ],
)
def test_unsafe_expected_record_is_rejected_before_hashing(manifest_root, monkeypatch, relative, checksum):
    manifest.MANIFEST.write_text(f"{checksum}  {relative}\n", encoding="utf-8")
    monkeypatch.setattr(manifest, "release_files", lambda: set())
    hashes = []
    monkeypatch.setattr(manifest, "digest", lambda path: hashes.append(path) or "a" * 64)
    monkeypatch.setattr(manifest.Path, "is_file", lambda _path: True)
    with pytest.raises(SystemExit) as error:
        manifest.main([])
    assert hashes == []
    assert "manifest line" in str(error.value)


@pytest.mark.skipif(os.name != "nt", reason="actual Windows junction security regression")
@pytest.mark.parametrize("enumerator", ["git", "fallback"])
def test_source_junction_rejected_before_descending(manifest_root, tmp_path, monkeypatch, enumerator):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private-key.txt").write_bytes(b"external secret fixture")
    junction = manifest_root / "json_api_forge"
    created = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)], capture_output=True)
    if created.returncode:
        pytest.skip("Junction creation unavailable")
    scanned = []
    original_scan = os.scandir

    def checked_scan(path):
        scanned.append(Path(path))
        assert Path(path) != junction, "Fallback descended into an external junction"
        return original_scan(path)

    try:
        assert not junction.is_symlink(), "Fixture must reproduce the junction/symlink distinction"
        if enumerator == "git":
            outputs = [manifest_root.as_posix(), b"json_api_forge/private-key.txt\0"]
            monkeypatch.setattr(manifest.subprocess, "check_output", lambda *_args, **_kwargs: outputs.pop(0))
        else:
            monkeypatch.setattr(manifest.subprocess, "check_output", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError()))
            monkeypatch.setattr(os, "scandir", checked_scan)
        with pytest.raises(ValueError, match="junction"):
            manifest.release_files()
        assert junction not in scanned
    finally:
        junction.rmdir()  # Removes only the verified fixture junction, never its target.


@pytest.mark.skipif(os.name != "nt", reason="actual Windows junction security regression")
def test_ignored_build_junction_is_not_scanned_or_rejected(manifest_root, tmp_path, monkeypatch):
    outside = tmp_path / "artifacts"
    outside.mkdir()
    (outside / "private.txt").write_bytes(b"ignored fixture")
    (manifest_root / "README.md").write_bytes(b"source")
    junction = manifest_root / "build"
    created = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)], capture_output=True)
    if created.returncode:
        pytest.skip("Junction creation unavailable")
    original_scan = os.scandir

    def checked_scan(path):
        assert Path(path) != junction, "Ignored junction was descended into"
        return original_scan(path)

    try:
        monkeypatch.setattr(manifest.subprocess, "check_output", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError()))
        monkeypatch.setattr(os, "scandir", checked_scan)
        assert manifest.release_files() == {"README.md"}
    finally:
        junction.rmdir()


def test_roundtrip_and_relocated_git_checkout_use_safe_fallback(manifest_root, monkeypatch):
    (manifest_root / "README.md").write_bytes(b"source")
    monkeypatch.setattr(manifest.subprocess, "check_output", lambda *_args, **_kwargs: "other-checkout")
    assert manifest.main(["--write"]) == 0
    assert manifest.main([]) == 0


@pytest.mark.skipif(os.name != "nt", reason="actual Windows junction security regression")
def test_expected_path_under_unlisted_junction_is_never_hashed(manifest_root, tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_bytes(b"outside secret fixture")
    junction = manifest_root / "json_api_forge"
    created = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)], capture_output=True)
    if created.returncode:
        pytest.skip("Junction creation unavailable")
    try:
        manifest.MANIFEST.write_text(f"{'a' * 64}  json_api_forge/private.txt\n", encoding="utf-8")
        monkeypatch.setattr(manifest, "release_files", lambda: set())
        hashes = []
        monkeypatch.setattr(manifest, "digest", lambda path: hashes.append(path) or "a" * 64)
        with pytest.raises(ValueError, match="junction"):
            manifest.main([])
        assert not hashes
    finally:
        junction.rmdir()
