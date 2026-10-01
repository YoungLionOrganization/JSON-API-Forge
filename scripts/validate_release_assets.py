#!/usr/bin/env python3
"""Validate and flatten a server build without accepting extra component assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def check_archive(path: Path) -> None:
    """Read every member so a matching checksum cannot hide a broken package."""
    try:
        if path.suffix in {".zip", ".whl"}:
            with zipfile.ZipFile(path) as archive:
                if not archive.infolist() or archive.testzip() is not None:
                    raise ValueError("Empty or corrupt ZIP package")
        else:
            with tarfile.open(path, "r:*") as archive:
                found = False
                for member in archive:
                    found = True
                    if member.isfile():
                        with archive.extractfile(member) as stream:
                            size = 0
                            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                                size += len(chunk)
                            if size != member.size:
                                raise ValueError("Truncated archive member")
                if not found:
                    raise ValueError("Empty TAR package")
    except (OSError, EOFError, tarfile.TarError, zipfile.BadZipFile, ValueError) as exc:
        raise ValueError(f"Invalid package archive: {path.name}") from exc


def validate(source: Path, output: Path, *, sha: str, run_id: str, run_attempt: str = "1") -> dict:
    expected = json.loads((ROOT / "release/assets.json").read_text())
    files = {}
    for path in source.rglob("*"):
        if path.is_symlink():
            raise ValueError("Build assets cannot be symlinks")
        if path.is_file():
            if path.name in files:
                raise ValueError(f"Duplicate asset: {path.name}")
            files[path.name] = path
    if "build.json" not in files:
        raise ValueError("Missing build provenance")
    proof = json.loads(files["build.json"].read_text())
    release = json.loads((ROOT / "release/release.json").read_text())
    if proof != {"sha": sha, "run_id": str(run_id), "run_attempt": str(run_attempt), "version": release["version"]}:
        raise ValueError("Build provenance does not match the selected commit/run/version")
    allowed = set(expected) | {name + ".sha256" for name in expected} | {"build.json", "SHA256SUMS", "PYTHON-SHA256SUMS"}
    if set(files) - allowed or set(expected) - set(files):
        raise ValueError("Incomplete or unexpected platform assets")
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("Output must be empty")
    manifest = {}
    for name in expected:
        checksum = files.get(name + ".sha256")
        actual = digest(files[name])
        if checksum is None or checksum.read_text().strip() != f"{actual}  {name}":
            raise ValueError(f"Missing or invalid checksum: {name}")
        check_archive(files[name])
        for asset in (name, name + ".sha256"):
            shutil.copyfile(files[asset], output / asset)
            manifest[asset] = digest(output / asset)
    (output / "release-build.json").write_text(json.dumps({**proof, "assets": manifest}, indent=2) + "\n")
    manifest["release-build.json"] = digest(output / "release-build.json")
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    args = parser.parse_args()
    validate(args.source, args.output, sha=args.sha, run_id=args.run_id, run_attempt=args.run_attempt)


if __name__ == "__main__":
    main()
