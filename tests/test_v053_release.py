from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def module(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_assets_require_exact_set_provenance_and_companion_checksums(tmp_path, monkeypatch):
    validator = module("validate_release_assets")
    root = tmp_path / "repo"
    (root / "release").mkdir(parents=True)
    (root / "release/assets.json").write_text(json.dumps(["server.zip"]))
    (root / "release/release.json").write_text(json.dumps({"version": "0.5.3"}))
    monkeypatch.setattr(validator, "ROOT", root)
    source = tmp_path / "source"
    source.mkdir()
    with zipfile.ZipFile(source / "server.zip", "w") as bundle:
        bundle.writestr("bin/forge", b"build")
    (source / "build.json").write_text(json.dumps({"sha": "a" * 40, "run_id": "12", "run_attempt": "1", "version": "0.5.3"}))
    with pytest.raises(ValueError, match="checksum"):
        validator.validate(source, tmp_path / "missing", sha="a" * 40, run_id="12")
    (source / "server.zip.sha256").write_text(validator.digest(source / "server.zip") + "  server.zip\n")
    manifest = validator.validate(source, tmp_path / "ok", sha="a" * 40, run_id="12")
    assert set(manifest) == {"server.zip", "server.zip.sha256", "release-build.json"}
    with pytest.raises(ValueError, match="provenance"):
        validator.validate(source, tmp_path / "wrong", sha="b" * 40, run_id="12")
    (source / "editor.zip").write_bytes(b"unexpected")
    with pytest.raises(ValueError, match="unexpected"):
        validator.validate(source, tmp_path / "extra", sha="a" * 40, run_id="12")
    (source / "editor.zip").unlink()
    (source / "server.zip").write_bytes(b"broken but correctly checksummed")
    (source / "server.zip.sha256").write_text(validator.digest(source / "server.zip") + "  server.zip\n")
    with pytest.raises(ValueError, match="archive"):
        validator.validate(source, tmp_path / "broken", sha="a" * 40, run_id="12")


def test_latest_failed_attempt_cannot_fall_back_and_remote_assets_must_match(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    publisher = module("publish_release")
    good = {
        "id": 1,
        "head_sha": "a" * 40,
        "head_branch": "main",
        "event": "push",
        "run_attempt": 1,
        "status": "completed",
        "conclusion": "success",
    }
    assert publisher.latest_success([good], "a" * 40) == good
    for failed in ({**good, "id": 2, "conclusion": "failure"}, {**good, "run_attempt": 2, "status": "in_progress"}):
        with pytest.raises(ValueError):
            publisher.latest_success([good, failed], "a" * 40)
    publisher.verify_remote({"assets": [{"name": "a", "digest": "sha256:abc"}]}, {"a": "abc"})
    with pytest.raises(ValueError):
        publisher.verify_remote({"assets": [{"name": "a", "digest": None}]}, {"a": "abc"})
    with pytest.raises(ValueError):
        publisher.verify_remote({"assets": [{"name": "editor", "digest": "sha256:abc"}]}, {"a": "abc"})
    publisher.verify_remote({"assets": []}, {"a": "abc"}, allow_partial=True)
    with pytest.raises(ValueError):
        publisher.verify_remote({"assets": [{"name": "a", "digest": "sha256:wrong"}]}, {"a": "abc"}, allow_partial=True)
