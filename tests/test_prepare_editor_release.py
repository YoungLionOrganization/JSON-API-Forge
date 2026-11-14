from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture
def staging(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("editor_staging_under_test", SCRIPTS / "prepare_editor_release.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def setup_staging(staging, monkeypatch, *, public=False, bad_proof=False, name="Editor"):
    plan = json.loads((SCRIPTS.parent / "release/components-v0.5.2.json").read_text())
    original = next(item for item in plan["components"] if item["name"] == name)
    sha = "a" * 40
    mutations = []
    release = {"draft": not public, "tag_name": original["tag"], "target_commitish": original["sha"], "assets": []}
    ref = {"object": {"type": "commit", "sha": original["sha"]}}
    monkeypatch.setattr(staging, "authorize", lambda repo: None)
    monkeypatch.setattr(staging, "verify_controller", lambda repo: None)
    monkeypatch.setattr(staging, "workflow_gate", lambda repo, name, source, branch: original["required_workflows"][name])
    monkeypatch.setattr(staging, "verify_gates", lambda repo, component: None)
    monkeypatch.setattr(staging.subprocess, "check_output", lambda *args, **kwargs: sha + "\n")

    def api(path, *, method="GET", data=None):
        if method != "GET":
            mutations.append((method, path, data))
            if "/git/refs/" in path:
                ref["object"]["sha"] = data["sha"]
            else:
                release.update(data)
                if "tag_name" not in data:
                    release["tag_name"] = "untagged-fixture"
        return copy.deepcopy(ref if "/git/" in path else release)

    def run(command, **kwargs):
        if command[1:3] == ["run", "download"]:
            folder = Path(command[-1]) / "release-assets"
            folder.mkdir(parents=True)
            for name in original["assets"]:
                (folder / name).write_bytes(b"fixture artifact")
            assets = {
                name: hashlib.sha256((folder / name).read_bytes()).hexdigest()
                for name in original["assets"]
                if name.endswith((".zip", ".exe", ".run", ".dmg"))
            }
            build = original["required_workflows"]["editor-build.yml"]
            proof = dict(
                sha="b" * 40 if bad_proof else sha,
                version="0.5.2",
                branch="Editor",
                call_client_revision=2,
                run_id=build["run_id"],
                run_attempt=build["run_attempt"],
                assets=assets,
            )
            (folder / "release-build.json").write_text(json.dumps(proof))
        elif command[1:3] == ["release", "upload"]:
            path = Path(command[4])
            mutations.append(("upload", path.name))
            release["assets"] = [item for item in release["assets"] if item["name"] != path.name]
            release["assets"].append({"name": path.name, "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()})
        else:
            assert kwargs["env"] == {"PYTHONIOENCODING": "utf-8"}
            assert "-I" in command

    monkeypatch.setattr(staging, "api", api)
    if name == "server":

        def validate_server(repo, sha, build, destination):
            destination.mkdir(exist_ok=True)
            assets = {}
            for asset in original["assets"]:
                if asset != "release-build.json":
                    (destination / asset).write_bytes(b"fixture server artifact")
                    assets[asset] = hashlib.sha256((destination / asset).read_bytes()).hexdigest()
            proof = {**build, "sha": "b" * 40 if bad_proof else sha, "version": "0.5.2", "assets": assets}
            (destination / "release-build.json").write_text(json.dumps(proof))
            return destination

        monkeypatch.setattr(staging, "server_packages", validate_server)
    monkeypatch.setattr(staging.subprocess, "run", run)
    return sha, release, ref, mutations


def test_verified_draft_replacement_updates_tag_only_after_all_uploads(staging, monkeypatch, tmp_path):
    sha, release, ref, mutations = setup_staging(staging, monkeypatch)
    report = tmp_path / "component.json"
    component = staging.prepare(sha, tmp_path, report)
    uploads = [item for item in mutations if item[0] == "upload"]
    assert len(uploads) == 26
    assert mutations[:26] == uploads
    assert ref["object"]["sha"] == release["target_commitish"] == component["sha"] == sha
    assert release["draft"] is True
    assert json.loads(report.read_text()) == component


@pytest.mark.parametrize("public,bad_proof", [(True, False), (False, True)])
def test_public_release_or_wrong_build_proof_cannot_mutate_assets(staging, monkeypatch, tmp_path, public, bad_proof):
    sha, _, _, mutations = setup_staging(staging, monkeypatch, public=public, bad_proof=bad_proof)
    with pytest.raises(ValueError):
        staging.prepare(sha, tmp_path, tmp_path / "component.json")
    assert not mutations


def test_unexpected_tag_cannot_be_overwritten(staging, monkeypatch, tmp_path):
    sha, _, ref, mutations = setup_staging(staging, monkeypatch)
    ref["object"]["sha"] = "c" * 40
    with pytest.raises(ValueError, match="Draft tag"):
        staging.prepare(sha, tmp_path, tmp_path / "component.json")
    assert not mutations


def test_wrong_checkout_cannot_execute_validation_or_change_draft(staging, monkeypatch, tmp_path):
    sha, _, _, mutations = setup_staging(staging, monkeypatch)
    monkeypatch.setattr(staging.subprocess, "check_output", lambda *args, **kwargs: "c" * 40)
    with pytest.raises(ValueError, match="Validation source"):
        staging.prepare(sha, tmp_path, tmp_path / "component.json")
    assert not mutations


@pytest.mark.parametrize("public,bad_proof", [(False, False), (False, True), (True, False)])
def test_restaging_server_uses_only_the_verified_build(staging, monkeypatch, tmp_path, public, bad_proof):
    sha, release, ref, mutations = setup_staging(staging, monkeypatch, public=public, bad_proof=bad_proof, name="server")
    report = tmp_path / "server.json"
    (tmp_path / "release/notes").mkdir(parents=True)
    (tmp_path / "release/notes/v0.5.2.md").write_text("Updated server notes")
    if public or bad_proof:
        with pytest.raises(ValueError):
            staging.prepare(sha, tmp_path, report, name="server")
        assert not mutations
        return

    component = staging.prepare(sha, tmp_path, report, name="server")
    assert [operation[1] for operation in mutations if operation[0] == "upload"] == sorted(component["assets"])
    assert ref["object"]["sha"] == release["target_commitish"] == component["sha"] == sha
    assert release["draft"] is True
    assert json.loads(report.read_text()) == component
