from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest


def checker(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / "scripts/check_manifest.py"
    spec = importlib.util.spec_from_file_location("manifest_boundary_check", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "export"
    root.mkdir()
    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setattr(module, "MANIFEST", root / "MANIFEST.sha256")
    return module, root


@pytest.mark.parametrize(
    "relative,digest",
    [
        ("../outside.txt", "a" * 64),
        ("/outside.txt", "a" * 64),
        ("C:/outside.txt", "a" * 64),
        ("source:stream", "a" * 64),
        ("folder\\outside.txt", "a" * 64),
        (".env", "a" * 64),
        ("build/private.txt", "a" * 64),
        ("source.py", "g" * 64),
        ("source.py", "abc"),
    ],
)
def test_manifest_rejects_invalid_records_before_reading_file_content(tmp_path, monkeypatch, relative, digest):
    module, root = checker(tmp_path, monkeypatch)
    (root / "source.py").write_text("source", encoding="utf-8")
    module.MANIFEST.write_text(f"{digest}  {relative}\n", encoding="utf-8")

    def forbidden_read(_path):
        raise AssertionError("Invalid records must not read or hash file content")

    monkeypatch.setattr(module, "digest", forbidden_read)
    with pytest.raises(SystemExit, match="Unsafe manifest line"):
        module.main([])


def force_export_scan(module, monkeypatch):
    def no_checkout(*args, **kwargs):
        raise subprocess.CalledProcessError(128, "git")

    monkeypatch.setattr(module.subprocess, "check_output", no_checkout)


@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_export_scan_rejects_external_linked_parents(tmp_path, monkeypatch, kind):
    module, root = checker(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "private-key.txt"
    secret.write_text("synthetic private fixture", encoding="utf-8")
    link = root / "linked"
    if kind == "junction":
        if os.name != "nt":
            pytest.skip("Windows junction test")
        result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
        if result.returncode:
            pytest.skip("Test filesystem cannot create a junction")
    else:
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("Test filesystem cannot create a symlink")
    try:
        force_export_scan(module, monkeypatch)
        with pytest.raises(ValueError, match="links or reparse points"):
            module.release_files()
        assert secret.read_text(encoding="utf-8") == "synthetic private fixture"
    finally:
        if kind == "junction":
            link.rmdir()
        else:
            link.unlink()


def test_ignored_directory_links_are_pruned_from_export_scan(tmp_path, monkeypatch):
    module, root = checker(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private-key.txt").write_text("synthetic fixture", encoding="utf-8")
    link = root / "build"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Test filesystem cannot create a symlink")
    try:
        (root / "source.py").write_text("source", encoding="utf-8")
        force_export_scan(module, monkeypatch)
        assert module.release_files() == {"source.py"}
    finally:
        link.unlink()


def test_valid_export_manifest_still_checks_file_changes_and_missing_files(tmp_path, monkeypatch):
    module, root = checker(tmp_path, monkeypatch)
    force_export_scan(module, monkeypatch)
    source = root / "source.py"
    source.write_text("source", encoding="utf-8")
    module.write_manifest()
    assert module.main([]) == 0
    source.write_text("changed", encoding="utf-8")
    with pytest.raises(SystemExit, match="hash mismatch"):
        module.main([])
    source.unlink()
    with pytest.raises(SystemExit, match="missing"):
        module.main([])
