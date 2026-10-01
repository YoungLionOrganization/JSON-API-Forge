#!/usr/bin/env python3
"""Manual, exact-commit server publisher. Never overwrite a public release."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

from validate_release_assets import ROOT, validate


def api(path, *, method="GET", data=None):
    command = ["gh", "api", "--method", method, path]
    if data is not None:
        command += ["--input", "-"]
    result = subprocess.run(command, input=json.dumps(data) if data is not None else None, text=True, check=True, capture_output=True)
    return json.loads(result.stdout) if result.stdout.strip() else None


def latest_success(runs: list[dict], sha: str) -> dict:
    eligible = [r for r in runs if r["head_sha"] == sha and r["head_branch"] == "main" and r["event"] == "push"]
    if not eligible:
        raise ValueError("No main push workflow run for the selected commit")
    latest = max(eligible, key=lambda r: (r["id"], r["run_attempt"]))
    if latest["status"] != "completed" or latest["conclusion"] != "success":
        raise ValueError("Latest workflow run/attempt is not successful")
    return latest


def unpack(archive: Path, destination: Path):
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            path = Path(member.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in member.filename or (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Unsafe artifact member")
        bundle.extractall(destination)


def verify_remote(release: dict, manifest: dict, *, allow_partial: bool = False):
    assets = {asset["name"]: asset for asset in release["assets"]}
    if len(assets) != len(release["assets"]) or set(assets) - set(manifest) or (not allow_partial and set(assets) != set(manifest)):
        raise ValueError("Remote release assets differ from the verified server bundle")
    for name in assets:
        expected = manifest[name]
        if assets[name].get("digest") != "sha256:" + expected:
            raise ValueError(f"Remote asset digest does not match: {name}")


def upload(repo: str, release_id: int, asset: Path):
    connection = http.client.HTTPSConnection("uploads.github.com", timeout=60)
    try:
        connection.putrequest("POST", f"/repos/{repo}/releases/{release_id}/assets?name={asset.name}")
        connection.putheader("Authorization", "Bearer " + os.environ["GH_TOKEN"])
        connection.putheader("Content-Type", "application/octet-stream")
        connection.putheader("Content-Length", str(asset.stat().st_size))
        connection.putheader("User-Agent", "JSON-API-Forge-release")
        connection.endheaders()
        with asset.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                connection.send(chunk)
        response = connection.getresponse()
        response.read()
        if response.status != 201:
            raise ValueError(f"Asset upload failed: {asset.name} (HTTP {response.status})")
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sha", required=True)
    parser.add_argument("--mode", choices=["validate", "draft", "publish"], required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.sha):
        raise ValueError("Source must be an immutable commit SHA")
    authorized = json.loads((ROOT / "release/authorized_publishers.json").read_text())
    if any(os.environ.get(key) not in authorized for key in ("GITHUB_ACTOR", "GITHUB_TRIGGERING_ACTOR")):
        raise ValueError("Actor is not an authorized publisher")
    repo = os.environ["GITHUB_REPOSITORY"]
    prefix = f"repos/{repo}"
    metadata = json.loads((ROOT / "release/release.json").read_text())
    source = api(f"{prefix}/commits/{args.sha}")
    if source["sha"] != args.sha:
        raise ValueError("Source commit mismatch")
    build = None
    gates = {}
    for workflow in metadata["required_workflows"]:
        runs = []
        for page in range(1, 11):
            batch = api(f"{prefix}/actions/workflows/{workflow}/runs?head_sha={args.sha}&per_page=100&page={page}")["workflow_runs"]
            runs.extend(batch)
            if len(batch) < 100:
                break
        else:
            raise ValueError("Workflow history exceeds verification limit")
        selected = latest_success(runs, args.sha)
        current = api(f"{prefix}/actions/runs/{selected['id']}")
        latest_success([current], args.sha)
        if current["run_attempt"] != selected["run_attempt"]:
            raise ValueError("Workflow attempt changed during verification")
        gates[workflow] = (current["id"], current["run_attempt"])
        if workflow == "server-builds.yml":
            build = current
    if build is None:
        raise ValueError("Missing server build")
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        subprocess.run(
            ["gh", "run", "download", str(build["id"]), "--repo", repo, "--name", metadata["artifact"], "--dir", str(root / "download")],
            check=True,
        )
        archive = root / "download" / (metadata["artifact"] + ".zip")
        unpack(archive, root / "expanded")
        manifest = validate(
            root / "expanded", root / "upload", sha=args.sha, run_id=str(build["id"]), run_attempt=str(build["run_attempt"])
        )
        if args.mode == "validate":
            print(f"Validated {len(manifest)} assets for {args.sha}")
            return
        tag = metadata["tag"]
        releases = []
        for page in range(1, 11):
            batch = api(f"{prefix}/releases?per_page=100&page={page}")
            releases.extend(batch)
            if len(batch) < 100:
                break
        else:
            raise ValueError("Release history exceeds verification limit")
        matches = [r for r in releases if r["tag_name"] == tag]
        if len(matches) > 1 or any(not r["draft"] for r in matches):
            raise ValueError("An existing public release must never be modified")
        # A tag must identify exactly the build commit; annotated tags are refused.
        refs = api(f"{prefix}/git/matching-refs/tags/{tag}")
        exact = [r for r in refs if r["ref"] == f"refs/tags/{tag}"]
        if exact and (exact[0]["object"]["type"] != "commit" or exact[0]["object"]["sha"] != args.sha):
            raise ValueError("Release tag does not identify the selected commit")
        if not exact:
            api(f"{prefix}/git/refs", method="POST", data={"ref": f"refs/tags/{tag}", "sha": args.sha})
        if matches:
            release = api(f"{prefix}/releases/{matches[0]['id']}")
            if not release["draft"]:
                raise ValueError("Release became public during validation")
            verify_remote(release, manifest, allow_partial=True)
        else:
            notes = (ROOT / f"release/notes/{tag}.md").read_text()
            release = api(
                f"{prefix}/releases",
                method="POST",
                data={"tag_name": tag, "target_commitish": args.sha, "name": f"JSON API Forge {tag}", "body": notes, "draft": True},
            )
        # Resume an interrupted draft upload without replacing verified assets.
        existing = {asset["name"] for asset in release["assets"]}
        for asset in sorted((root / "upload").iterdir()):
            if asset.name not in existing:
                upload(repo, release["id"], asset)
        release = api(f"{prefix}/releases/{release['id']}")
        verify_remote(release, manifest)
        if args.mode == "publish":
            # Recheck gates immediately before the irreversible public transition.
            for workflow in metadata["required_workflows"]:
                runs = api(f"{prefix}/actions/workflows/{workflow}/runs?head_sha={args.sha}&per_page=100")["workflow_runs"]
                current = latest_success(runs, args.sha)
                if (current["id"], current["run_attempt"]) != gates[workflow]:
                    raise ValueError("Workflow changed during publication; validate the new build first")
            ref = api(f"{prefix}/git/ref/tags/{tag}")
            if ref["object"]["type"] != "commit" or ref["object"]["sha"] != args.sha:
                raise ValueError("Release tag changed during publication")
            release = api(f"{prefix}/releases/{release['id']}")
            if not release["draft"]:
                raise ValueError("Release became public during validation")
            verify_remote(release, manifest)
            release = api(f"{prefix}/releases/{release['id']}", method="PATCH", data={"draft": False})
        print(release["html_url"])


if __name__ == "__main__":
    main()
