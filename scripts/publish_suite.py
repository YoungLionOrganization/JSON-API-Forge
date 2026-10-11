#!/usr/bin/env python3
"""Publish the reviewed component drafts together, with a resumable preflight."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from publish_release import normalize_sha, verify_remote
from validate_release_assets import ROOT, digest

COMPONENTS = ("server", "Editor", "python-library", "exampleApps")
WORKFLOWS = {
    "server": {"ci.yml", "codeql.yml", "server-builds.yml"},
    "Editor": {"editor-build.yml", "editor-codeql.yml"},
    "python-library": {"python-library.yml", "codeql.yml"},
    "exampleApps": {"example-apps.yml", "codeql.yml"},
}


def api(path: str, *, method: str = "GET", data=None):
    command = ["gh", "api", "--method", method, path]
    if data is not None:
        command += ["--input", "-"]
    for attempt in range(3 if method == "GET" else 1):
        try:
            result = subprocess.run(
                command, input=json.dumps(data) if data is not None else None, text=True, encoding="utf-8", capture_output=True, timeout=60
            )
        except subprocess.TimeoutExpired:
            if method != "GET" or attempt == 2:
                raise RuntimeError("GitHub request timed out; rerun to reconcile the current release state") from None
            time.sleep(attempt + 1)
            continue
        if result.returncode == 0:
            return json.loads(result.stdout) if result.stdout.strip() else None
        if method == "GET" and attempt < 2:
            time.sleep(attempt + 1)
    raise RuntimeError(f"GitHub request failed: {method} {path}. Rerun after checking connectivity and permissions.")


def authorize(repo: str) -> None:
    authorized = json.loads((ROOT / "release/authorized_publishers.json").read_text(encoding="utf-8"))
    if os.environ.get("GITHUB_REPOSITORY") != repo:
        raise ValueError("Publication must run in the canonical repository")
    if any(os.environ.get(key) not in authorized for key in ("GITHUB_ACTOR", "GITHUB_TRIGGERING_ACTOR")):
        raise ValueError("Actor is not an authorized publisher")


def load_plan(path: Path) -> dict:
    plan = json.loads(path.read_text(encoding="utf-8"))
    if (
        plan.get("schema_version") != 1
        or plan.get("repository") != "YoungLionOrganization/JSON-API-Forge"
        or plan.get("version") != "0.5.2"
    ):
        raise ValueError("Unsupported publication plan identity/version")
    components = plan.get("components", [])
    if tuple(item.get("name") for item in components) != COMPONENTS:
        raise ValueError("Publication plan must include the four components in order")
    for component in components:
        if normalize_sha(component["sha"]) != component["sha"] or type(component["release_id"]) is not int or component["release_id"] < 1:
            raise ValueError("Invalid prepared source/release identity")
        name = component["name"]
        branch = "main" if name == "server" else name
        tag = "v0.5.2" if name == "server" else f"{'editor' if name == 'Editor' else name}-v0.5.2"
        if component["branch"] != branch or component["tag"] != tag:
            raise ValueError("Prepared component branch/tag mismatch")
        assets = component.get("assets", {})
        if "release-build.json" not in assets or not assets:
            raise ValueError("Missing publication proof manifest")
        for asset_name, checksum in assets.items():
            if not re.fullmatch(r"[A-Za-z0-9._-]+", asset_name) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
                raise ValueError("Unsafe asset name or malformed digest")
        gates = component["required_workflows"]
        if set(gates) != WORKFLOWS[name] or component["build_workflow"] not in gates:
            raise ValueError("Missing prepared build gate")
        for name, gate in gates.items():
            if not re.fullmatch(r"[A-Za-z0-9_-]+\.yml", name) or any(
                type(gate.get(key)) is not int or gate[key] < 1 for key in ("run_id", "run_attempt")
            ):
                raise ValueError("Invalid workflow gate identity")
    return plan


def select_components(plan: dict, selection: str, source_sha: str = "") -> list[dict]:
    chosen = list(plan["components"]) if selection == "all" else [item for item in plan["components"] if item["name"] == selection]
    if not chosen:
        raise ValueError("Unknown release component")
    if source_sha.strip():
        sha = normalize_sha(source_sha)
        target = "server" if selection == "all" else selection
        candidate = next(item for item in chosen if item["name"] == target)
        if sha != candidate["sha"]:
            raise ValueError("Source SHA differs from the prepared candidate. Update and verify the publication plan first.")
    return chosen


def latest_success(runs: list[dict], sha: str, branch: str) -> dict:
    eligible = [run for run in runs if run["head_sha"] == sha and run["head_branch"] == branch and run["event"] == "push"]
    if not eligible:
        raise ValueError(f"No {branch} push workflow exists for the prepared source")
    latest = max(eligible, key=lambda run: (run["id"], run["run_attempt"]))
    if latest["status"] != "completed" or latest["conclusion"] != "success":
        raise ValueError(f"Latest {branch} workflow run/attempt is not successful")
    return latest


def workflow_gate(repo: str, workflow: str, sha: str, branch: str) -> dict:
    runs = []
    for page in range(1, 11):
        batch = api(f"repos/{repo}/actions/workflows/{workflow}/runs?head_sha={sha}&per_page=100&page={page}")["workflow_runs"]
        runs.extend(batch)
        if len(batch) < 100:
            break
    else:
        raise ValueError("Workflow history exceeds the bounded verification limit")
    selected = latest_success(runs, sha, branch)
    current = api(f"repos/{repo}/actions/runs/{selected['id']}")
    latest_success([current], sha, branch)
    if current["run_attempt"] != selected["run_attempt"]:
        raise ValueError("Workflow attempt changed during verification")
    return {"run_id": current["id"], "run_attempt": current["run_attempt"]}


def verify_gates(repo: str, component: dict) -> None:
    for workflow, expected in component["required_workflows"].items():
        current = workflow_gate(repo, workflow, component["sha"], component["branch"])
        if current != expected:
            raise ValueError(f"{component['name']}: build/workflow changed. Reverify packages and refresh the publication plan.")


def tag_ref(repo: str, component: dict) -> dict | None:
    refs = api(f"repos/{repo}/git/matching-refs/tags/{component['tag']}")
    exact = [ref for ref in refs if ref["ref"] == f"refs/tags/{component['tag']}"]
    if len(exact) > 1:
        raise ValueError("Duplicate release tag")
    if exact and (exact[0]["object"]["type"] != "commit" or exact[0]["object"]["sha"] != component["sha"]):
        raise ValueError(f"{component['name']}: release tag does not identify the prepared commit")
    return exact[0] if exact else None


def checked_release(repo: str, component: dict) -> dict:
    release = api(f"repos/{repo}/releases/{component['release_id']}")
    if release["tag_name"] != component["tag"] or release["target_commitish"] != component["sha"]:
        raise ValueError(f"{component['name']}: prepared release identity changed")
    verify_remote(release, component["assets"])
    ref = tag_ref(repo, component)
    if not release["draft"] and ref is None:
        raise ValueError("Public release is missing its verified tag")
    return release


def verify_proof(component: dict, proof: dict) -> None:
    build = component["required_workflows"][component["build_workflow"]]
    if (
        proof.get("sha") != component["sha"]
        or proof.get("version") != "0.5.2"
        or str(proof.get("run_id")) != str(build["run_id"])
        or str(proof.get("run_attempt")) != str(build["run_attempt"])
    ):
        raise ValueError("Release proof does not match the prepared source/build/attempt")
    expected = {name: checksum for name, checksum in component["assets"].items() if name != "release-build.json"}
    if component["name"] == "Editor":
        expected = {name: checksum for name, checksum in expected.items() if name.endswith((".zip", ".exe", ".run", ".dmg"))}
        if proof.get("call_client_revision") != 2 or len(expected) != 12:
            raise ValueError("Editor proof must cover all 12 binaries and call-client revision 2")
    if component["name"] != "server" and proof.get("branch") != component["branch"]:
        raise ValueError("Release proof branch mismatch")
    if proof.get("assets") != expected:
        raise ValueError("Release proof asset set differs from the reviewed manifest")


def download_proof(repo: str, component: dict, release: dict) -> dict:
    asset = next(item for item in release["assets"] if item["name"] == "release-build.json")
    if not 1 <= asset["size"] <= 1024 * 1024:
        raise ValueError("Publication proof is outside the supported size bound")
    with tempfile.TemporaryDirectory(prefix="forge-release-proof-") as temporary:
        command = ["gh", "release", "download", component["tag"], "--repo", repo, "--pattern", "release-build.json", "--dir", temporary]
        for attempt in range(3):
            try:
                result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=60)
            except subprocess.TimeoutExpired:
                if attempt == 2:
                    raise RuntimeError("Publication proof download timed out") from None
                time.sleep(attempt + 1)
                continue
            if result.returncode == 0:
                break
            path = Path(temporary) / "release-build.json"
            path.unlink(missing_ok=True)
            if attempt < 2:
                time.sleep(attempt + 1)
        else:
            raise RuntimeError("Could not download the small release proof; check connectivity and permissions")
        path = Path(temporary) / "release-build.json"
        if digest(path) != component["assets"]["release-build.json"]:
            raise ValueError("Downloaded publication proof digest mismatch")
        proof = json.loads(path.read_text(encoding="utf-8"))
    verify_proof(component, proof)
    return proof


def verify_controller(repo: str) -> None:
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise ValueError("Publish from the main workflow branch")
    sha = normalize_sha(os.environ.get("GITHUB_SHA", ""))
    for workflow in ("ci.yml", "codeql.yml"):
        workflow_gate(repo, workflow, sha, "main")


def run_suite(plan: dict, components: list[dict], mode: str, *, report_path: Path) -> dict:
    repo = plan["repository"]
    authorize(repo)
    report = {"version": plan["version"], "mode": mode, "status": "validating", "components": []}

    def save():
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")

    try:
        # Finish every component's checks before the first tag or public transition.
        for component in components:
            verify_gates(repo, component)
            release = checked_release(repo, component)
            download_proof(repo, component, release)
            report["components"].append(
                {
                    "name": component["name"],
                    "tag": component["tag"],
                    "sha": component["sha"],
                    "url": release["html_url"],
                    "status": "ready" if release["draft"] else "already_public",
                }
            )
            save()
        if mode != "publish":
            report["status"] = "ready"
            save()
            return report
        verify_controller(repo)
        # Revalidate all gates after the potentially slow proof downloads.
        for component in components:
            verify_gates(repo, component)
            checked_release(repo, component)
        for component in components:
            if tag_ref(repo, component) is None:
                api(f"repos/{repo}/git/refs", method="POST", data={"ref": f"refs/tags/{component['tag']}", "sha": component["sha"]})
        for component, entry in zip(components, report["components"], strict=True):
            verify_gates(repo, component)
            release = checked_release(repo, component)
            if release["draft"]:
                release = api(f"repos/{repo}/releases/{component['release_id']}", method="PATCH", data={"draft": False})
                if release["draft"]:
                    raise ValueError("GitHub did not complete the public transition")
                verify_remote(release, component["assets"])
                checked_release(repo, component)
                entry.update(status="published", url=release["html_url"])
            else:
                entry.update(status="already_public", url=release["html_url"])
            save()
        report["status"] = "published"
        save()
        return report
    except Exception as exc:
        report.update(status="failed", error=str(exc))
        save()
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component", choices=("all", *COMPONENTS), default="all")
    parser.add_argument("--mode", choices=("validate", "draft", "publish"), default="validate")
    parser.add_argument("--source-sha", default="", help="Optional prepared source check; in all mode applies only to Main")
    parser.add_argument("--plan", type=Path, default=ROOT / "release/components-v0.5.2.json")
    parser.add_argument("--report", type=Path, default=Path("publication-report.json"))
    args = parser.parse_args()
    plan = load_plan(args.plan)
    chosen = select_components(plan, args.component, args.source_sha)
    report = run_suite(plan, chosen, args.mode, report_path=args.report)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
