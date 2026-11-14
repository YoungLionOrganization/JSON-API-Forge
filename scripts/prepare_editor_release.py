#!/usr/bin/env python3
"""Restage an unpublished component draft from its complete, successful build."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from publish_release import unpack
from publish_suite import (
    api,
    authorize,
    load_plan,
    normalize_sha,
    verify_controller,
    verify_gates,
    verify_proof,
    verify_remote,
    workflow_gate,
)
from validate_release_assets import ROOT, digest, validate


def draft(repo: str, component: dict) -> dict:
    release = api(f"repos/{repo}/releases/{component['release_id']}")
    if not release.get("draft") or release.get("tag_name") != component["tag"]:
        raise ValueError("Only the existing unpublished component draft may be restaged")
    return release


def tag(repo: str, component: dict, original_sha: str) -> dict:
    ref = api(f"repos/{repo}/git/ref/tags/{component['tag']}")
    if ref["object"]["type"] != "commit" or ref["object"]["sha"] not in {original_sha, component["sha"]}:
        raise ValueError("Draft tag no longer identifies the reviewed old or replacement source")
    return ref


def editor_packages(repo: str, sha: str, build: dict, temporary: Path, source: Path) -> Path:
    download = temporary / "download"
    subprocess.run(
        [
            "gh",
            "run",
            "download",
            str(build["run_id"]),
            "--repo",
            repo,
            "--name",
            "JSON-API-Forge-Editor-v0.5.2-release-assets",
            "--dir",
            str(download),
        ],
        check=True,
        timeout=600,
    )
    proofs = list(download.rglob("release-build.json"))
    if len(proofs) != 1 or any(path.is_symlink() for path in download.rglob("*")):
        raise ValueError("Expected one regular build proof in the verified artifact")
    folder = proofs[0].parent
    # Run the exact reviewed validator without passing publisher credentials.
    subprocess.run(
        [
            sys.executable,
            "-I",
            str(source / "editor/packaging/validate-release.py"),
            str(folder),
            "--sha",
            sha,
            "--run-id",
            str(build["run_id"]),
        ],
        check=True,
        timeout=300,
        env={"PYTHONIOENCODING": "utf-8"},
    )
    return folder


def server_packages(repo: str, sha: str, build: dict, temporary: Path) -> Path:
    artifact = json.loads((ROOT / "release/release.json").read_text(encoding="utf-8"))["artifact"]
    download = temporary / "download"
    subprocess.run(
        ["gh", "run", "download", str(build["run_id"]), "--repo", repo, "--name", artifact, "--dir", str(download)],
        check=True,
        timeout=600,
    )
    expanded = temporary / "expanded"
    unpack(download / (artifact + ".zip"), expanded)
    folder = temporary / "upload"
    validate(expanded, folder, sha=sha, run_id=str(build["run_id"]), run_attempt=str(build["run_attempt"]))
    return folder


def prepare(sha: str, source: Path, report: Path, *, name: str = "Editor") -> dict:
    repo = "YoungLionOrganization/JSON-API-Forge"
    authorize(repo)
    verify_controller(repo)
    sha = normalize_sha(sha)
    plan = load_plan(ROOT / "release/components-v0.5.2.json")
    original = next(item for item in plan["components"] if item["name"] == name)
    source = source.resolve(strict=True)
    checked_out = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if checked_out != sha:
        raise ValueError("Validation source does not match the selected component commit")
    component = {**original, "sha": sha}
    component["required_workflows"] = {
        workflow: workflow_gate(repo, workflow, sha, original["branch"]) for workflow in original["required_workflows"]
    }
    build = component["required_workflows"][component["build_workflow"]]
    component["validation_run"] = build["run_id"]
    release = draft(repo, component)
    if release["target_commitish"] not in {original["sha"], sha}:
        raise ValueError("Draft source changed outside the reviewed replacement")
    tag(repo, component, original["sha"])
    notes = (source / "release/notes/v0.5.2.md").read_text(encoding="utf-8") if name == "server" else None

    with tempfile.TemporaryDirectory(prefix="forge-component-restage-") as temporary:
        staging = Path(temporary)
        if name == "Editor":
            folder = editor_packages(repo, sha, build, staging, source)
        else:
            folder = server_packages(repo, sha, build, staging)
        expected = set(original["assets"])
        if any(not (folder / name).is_file() for name in expected):
            raise ValueError("Replacement does not contain the complete existing asset set")
        component["assets"] = {name: digest(folder / name) for name in sorted(expected)}
        proof = json.loads((folder / "release-build.json").read_text(encoding="utf-8"))
        verify_proof(component, proof)
        verify_gates(repo, component)
        remote = draft(repo, component)
        if {item["name"] for item in remote["assets"]} - expected:
            raise ValueError("Unexpected draft assets require separate review")

        for name, checksum in component["assets"].items():
            current = draft(repo, component)
            asset = next((item for item in current["assets"] if item["name"] == name), None)
            if asset is not None and asset.get("digest") == "sha256:" + checksum:
                continue
            subprocess.run(
                ["gh", "release", "upload", component["tag"], str(folder / name), "--repo", repo, "--clobber"],
                check=True,
                timeout=600,
            )
        verify_remote(draft(repo, component), component["assets"])
        verify_gates(repo, component)
        ref = tag(repo, component, original["sha"])
        draft(repo, component)
        if ref["object"]["sha"] != sha:
            # Move only the tag of the draft whose replacement assets were just verified.
            api(f"repos/{repo}/git/refs/tags/{component['tag']}", method="PATCH", data={"sha": sha, "force": True})
        current = draft(repo, component)
        body = current.get("body") or ""
        marker = "### Included Editor maintenance"
        if notes is not None:
            body = notes
        elif marker not in body:
            body += (
                "\n\n" + marker + "\n\n"
                "Includes isolated document undo history, preserved workspace selection and unsaved buffers, "
                "serialized remote saves and revision checks, and pending-save cleanup after failure/cancellation. "
                "Also includes the latest session/message/file/note isolation, JSON precision, mutation guards "
                "and packaging fixes. All six native targets and sanitizer checks are required on the exact build commit.\n"
            )
        api(f"repos/{repo}/releases/{component['release_id']}", method="PATCH", data={"target_commitish": sha, "body": body})
        verify_remote(draft(repo, component), component["assets"])
        tag(repo, component, sha)
        verify_gates(repo, component)

    report.write_text(json.dumps(component, indent=2) + "\n", encoding="utf-8")
    print("FORGE_RESTAGED_COMPONENT_PLAN_BEGIN")
    print(json.dumps(component, indent=2))
    print("FORGE_RESTAGED_COMPONENT_PLAN_END")
    return component


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--component", choices=("Editor", "server"), default="Editor")
    parser.add_argument("--report", type=Path, default=Path("prepared-component.json"))
    args = parser.parse_args()
    prepare(args.sha, args.source, args.report, name=args.component)


if __name__ == "__main__":
    main()
