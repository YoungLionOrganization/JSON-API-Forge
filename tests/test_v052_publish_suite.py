from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture
def suite(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("suite_under_test", SCRIPTS / "publish_suite.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def plan(suite):
    return suite.load_plan(SCRIPTS.parent / "release/components-v0.5.2.json")


@pytest.mark.parametrize("value", ["a" * 40, "  " + "A" * 40 + "\r\n\t", "\n" + "a" * 40 + "  "])
def test_clipboard_whitespace_is_normalized(suite, value):
    assert suite.normalize_sha(value) == "a" * 40


@pytest.mark.parametrize("value", ["a" * 39, "a" * 41, "a" * 20 + " " + "a" * 20, "a" * 40 + ";", "\ufeff" + "a" * 40])
def test_non_sha_input_is_still_rejected(suite, value):
    with pytest.raises(ValueError, match="commit SHA"):
        suite.normalize_sha(value)


def test_default_all_needs_no_sha_and_legacy_input_only_checks_main(suite, plan):
    assert suite.select_components(plan, "all") == plan["components"]
    sha = plan["components"][0]["sha"]
    assert suite.select_components(plan, "all", "    " + sha + "  ") == plan["components"]
    with pytest.raises(ValueError, match="differs"):
        suite.select_components(plan, "all", "b" * 40)
    with pytest.raises(ValueError, match="Unknown"):
        suite.select_components(plan, "other")
    assert len(suite.select_components(plan, "Editor")) == 1


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(repository="attacker/fork"),
        lambda p: p["components"].reverse(),
        lambda p: p["components"][0].update(tag="editor-v0.5.2"),
        lambda p: p["components"][0]["required_workflows"].pop("codeql.yml"),
        lambda p: p["components"][0]["assets"].update({"../unsafe.zip": "a" * 64}),
        lambda p: p["components"][0]["assets"].update({"server.zip": "bad"}),
    ],
)
def test_modified_plan_is_rejected(suite, plan, tmp_path, mutate):
    mutate(plan)
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError):
        suite.load_plan(path)


def test_failed_latest_attempt_or_wrong_branch_cannot_use_old_success(suite):
    run = {
        "id": 1,
        "head_sha": "a" * 40,
        "head_branch": "Editor",
        "event": "push",
        "run_attempt": 1,
        "status": "completed",
        "conclusion": "success",
    }
    assert suite.latest_success([run], "a" * 40, "Editor") == run
    for new in [{**run, "id": 2, "conclusion": "failure"}, {**run, "run_attempt": 2, "status": "in_progress"}]:
        with pytest.raises(ValueError):
            suite.latest_success([run, new], "a" * 40, "Editor")
    with pytest.raises(ValueError):
        suite.latest_success([run], "a" * 40, "main")


def environment(suite, monkeypatch, plan, *, public=(), corrupt=None, failure=None, uncertain=False):
    monkeypatch.setattr(suite, "authorize", lambda repo: None)
    monkeypatch.setattr(suite, "verify_controller", lambda repo: None)
    calls = []
    tags = {}
    releases = {}
    for component in plan["components"]:
        name = component["name"]
        releases[component["release_id"]] = {
            "id": component["release_id"],
            "tag_name": component["tag"],
            "target_commitish": component["sha"],
            "draft": name not in public,
            "html_url": "https://github.com/test/" + name,
            "assets": [{"name": filename, "digest": "sha256:" + digest, "size": 100} for filename, digest in component["assets"].items()],
        }
        if name in public:
            tags[component["tag"]] = {"ref": "refs/tags/" + component["tag"], "object": {"type": "commit", "sha": component["sha"]}}
        if name == corrupt:
            releases[component["release_id"]]["assets"][0]["digest"] = "sha256:wrong"

    def gates(repo, component):
        calls.append(("gate", component["name"]))

    def proof(repo, component, release):
        calls.append(("proof", component["name"]))

    def api(path, *, method="GET", data=None):
        calls.append((method, path))
        if "/git/matching-refs/tags/" in path:
            tag = path.split("/tags/")[-1]
            return [copy.deepcopy(tags[tag])] if tag in tags else []
        if path.endswith("/git/refs"):
            tag = data["ref"].split("refs/tags/")[-1]
            tags[tag] = {"ref": data["ref"], "object": {"type": "commit", "sha": data["sha"]}}
            return copy.deepcopy(tags[tag])
        release_id = int(path.rsplit("/", 1)[-1])
        release = releases[release_id]
        if method == "PATCH":
            assert data == {"draft": False}
            if release["tag_name"] == failure:
                if uncertain:
                    release["draft"] = False
                raise RuntimeError("interrupted publication")
            release["draft"] = False
        return copy.deepcopy(release)

    monkeypatch.setattr(suite, "api", api)
    monkeypatch.setattr(suite, "verify_gates", gates)
    monkeypatch.setattr(suite, "download_proof", proof)
    return calls, releases, tags


def test_all_checks_finish_before_any_public_transition(suite, plan, monkeypatch, tmp_path):
    calls, releases, tags = environment(suite, monkeypatch, plan)
    report = suite.run_suite(plan, plan["components"], "publish", report_path=tmp_path / "report.json")
    first_write = next(index for index, call in enumerate(calls) if call[0] in {"POST", "PATCH"})
    assert {call[1] for call in calls[:first_write] if call[0] == "proof"} == set(suite.COMPONENTS)
    assert len([call for call in calls if call[0] == "PATCH"]) == 4
    assert not any(release["draft"] for release in releases.values())
    assert len(tags) == 4 and report["status"] == "published"


def test_corrupt_last_component_prevents_every_write(suite, plan, monkeypatch, tmp_path):
    calls, releases, tags = environment(suite, monkeypatch, plan, corrupt="exampleApps")
    report_path = tmp_path / "report.json"
    with pytest.raises(ValueError, match="digest"):
        suite.run_suite(plan, plan["components"], "publish", report_path=report_path)
    assert all(release["draft"] for release in releases.values())
    assert not tags and not any(call[0] in {"POST", "PATCH"} for call in calls)
    assert json.loads(report_path.read_text())["status"] == "failed"


@pytest.mark.parametrize("mode", ["validate", "draft"])
def test_read_only_modes_never_create_tags_or_publish(suite, plan, monkeypatch, tmp_path, mode):
    calls, releases, tags = environment(suite, monkeypatch, plan)
    report = suite.run_suite(plan, plan["components"], mode, report_path=tmp_path / "report.json")
    assert report["status"] == "ready" and not tags
    assert not any(call[0] in {"POST", "PATCH"} for call in calls)


def test_partial_publication_and_uncertain_response_are_resumable(suite, plan, monkeypatch, tmp_path):
    calls, releases, tags = environment(suite, monkeypatch, plan, failure="python-library-v0.5.2", uncertain=True)
    with pytest.raises(RuntimeError, match="interrupted"):
        suite.run_suite(plan, plan["components"], "publish", report_path=tmp_path / "report.json")
    public = [component["name"] for component in plan["components"] if not releases[component["release_id"]]["draft"]]
    assert public == ["server", "Editor", "python-library"]
    calls, releases, tags = environment(suite, monkeypatch, plan, public=public)
    report = suite.run_suite(plan, plan["components"], "publish", report_path=tmp_path / "retry.json")
    assert len([call for call in calls if call[0] == "PATCH"]) == 1
    assert [entry["status"] for entry in report["components"]] == ["already_public"] * 3 + ["published"]


def test_already_public_mismatched_assets_are_not_overwritten(suite, plan, monkeypatch, tmp_path):
    calls, _, _ = environment(suite, monkeypatch, plan, public=["server"], corrupt="server")
    with pytest.raises(ValueError):
        suite.run_suite(plan, plan["components"], "publish", report_path=tmp_path / "report.json")
    assert not any(call[0] in {"POST", "PATCH"} for call in calls)


def test_changed_tag_or_draft_source_blocks_publication(suite, plan, monkeypatch, tmp_path):
    calls, releases, tags = environment(suite, monkeypatch, plan, public=["server"])
    tags["v0.5.2"]["object"]["sha"] = "f" * 40
    with pytest.raises(ValueError, match="tag"):
        suite.run_suite(plan, plan["components"], "publish", report_path=tmp_path / "tag.json")
    tags.clear()
    releases[plan["components"][0]["release_id"]]["target_commitish"] = "f" * 40
    with pytest.raises(ValueError, match="identity"):
        suite.run_suite(plan, plan["components"], "publish", report_path=tmp_path / "source.json")


@pytest.mark.parametrize("component_name", ["server", "Editor", "python-library", "exampleApps"])
def test_proofs_bind_the_asset_set_to_exact_build_attempt(suite, plan, component_name):
    component = next(item for item in plan["components"] if item["name"] == component_name)
    gate = component["required_workflows"][component["build_workflow"]]
    assets = {name: digest for name, digest in component["assets"].items() if name != "release-build.json"}
    if component_name == "Editor":
        assets = {name: digest for name, digest in assets.items() if name.endswith((".zip", ".run", ".exe", ".dmg"))}
    proof = {
        "sha": component["sha"],
        "version": "0.5.2",
        "branch": component["branch"],
        "run_id": str(gate["run_id"]),
        "run_attempt": str(gate["run_attempt"]),
        "assets": assets,
        "call_client_revision": 2,
    }
    suite.verify_proof(component, proof)
    with pytest.raises(ValueError, match="attempt"):
        suite.verify_proof(component, {**proof, "run_attempt": "999"})
    with pytest.raises(ValueError, match="asset set"):
        suite.verify_proof(component, {**proof, "assets": {}})


def test_controller_checks_require_main_and_successful_ci(suite, monkeypatch):
    monkeypatch.setenv("GITHUB_REF", "refs/heads/other")
    with pytest.raises(ValueError, match="main"):
        suite.verify_controller("repo")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    calls = []
    monkeypatch.setattr(suite, "workflow_gate", lambda *args: calls.append(args))
    suite.verify_controller("repo")
    assert [call[1] for call in calls] == ["ci.yml", "codeql.yml"]


def test_failed_controller_prevents_every_release_write(suite, plan, monkeypatch, tmp_path):
    calls, _, tags = environment(suite, monkeypatch, plan)

    def fail(repo):
        raise ValueError("controller CI failed")

    monkeypatch.setattr(suite, "verify_controller", fail)
    with pytest.raises(ValueError, match="controller CI"):
        suite.run_suite(plan, plan["components"], "publish", report_path=tmp_path / "report.json")
    assert not tags and not any(call[0] in {"POST", "PATCH"} for call in calls)


def test_rerun_attempt_requires_reverified_plan(suite, plan, monkeypatch):
    component = plan["components"][0]

    def changed(repo, workflow, sha, branch):
        gate = component["required_workflows"][workflow]
        return {**gate, "run_attempt": gate["run_attempt"] + 1}

    monkeypatch.setattr(suite, "workflow_gate", changed)
    with pytest.raises(ValueError, match="changed"):
        suite.verify_gates(plan["repository"], component)


def test_only_read_requests_retry_and_write_timeout_is_not_replayed(suite, monkeypatch):
    calls = []
    monkeypatch.setattr(suite.time, "sleep", lambda _: None)

    def failed(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(suite.subprocess, "run", failed)
    with pytest.raises(RuntimeError):
        suite.api("repos/repo/releases")
    assert len(calls) == 3
    calls.clear()
    with pytest.raises(RuntimeError):
        suite.api("repos/repo/releases/1", method="PATCH", data={"draft": False})
    assert len(calls) == 1

    def timeout(*args, **kwargs):
        calls.append(args)
        raise subprocess.TimeoutExpired("gh", 60)

    calls.clear()
    monkeypatch.setattr(suite.subprocess, "run", timeout)
    with pytest.raises(RuntimeError, match="timed out"):
        suite.api("repos/repo/releases/1", method="PATCH", data={"draft": False})
    assert len(calls) == 1


def test_publisher_authority_and_canonical_repo(suite, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "fork/repo")
    with pytest.raises(ValueError, match="canonical"):
        suite.authorize("YoungLionOrganization/JSON-API-Forge")
    monkeypatch.setenv("GITHUB_REPOSITORY", "YoungLionOrganization/JSON-API-Forge")
    monkeypatch.setenv("GITHUB_ACTOR", "Cavanshirpro")
    monkeypatch.setenv("GITHUB_TRIGGERING_ACTOR", "someone_else")
    with pytest.raises(ValueError, match="authorized"):
        suite.authorize("YoungLionOrganization/JSON-API-Forge")
    monkeypatch.setenv("GITHUB_TRIGGERING_ACTOR", "Cavanshirpro")
    suite.authorize("YoungLionOrganization/JSON-API-Forge")
