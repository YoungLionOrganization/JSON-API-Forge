#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
import re
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "MANIFEST.sha256"


def digest(path: Path) -> str:
    if not _regular_release_file(path.relative_to(ROOT).as_posix()):
        raise ValueError(f"Not a regular release file: {path}")
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _release_path(relative: str) -> bool:
    parts = relative.split("/")
    if (
        not relative
        or any(part in {"", ".", ".."} for part in parts)
        or "\\" in relative
        or ":" in relative
    ):
        raise ValueError(f"Unsafe release path: {relative!r}")
    if any(ord(char) < 32 or ord(char) == 127 for char in relative):
        raise ValueError(f"Control character in release path: {relative!r}")
    ignored = {
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
        "htmlcov",
    }
    if any(part in ignored or part.endswith(".egg-info") for part in parts):
        return False
    if parts[0] in {"data", "media", "logs", "stage", "artifacts"} or parts[
        0
    ].startswith("build-"):
        return False
    name = parts[-1]
    return not (
        name == "MANIFEST.sha256"
        or name.startswith(".coverage")
        or name in {"coverage.json", "coverage.xml"}
        or (name.startswith(".env") and name != ".env.example")
        or name.endswith((".pyc", ".pyo"))
    )


def _linked(metadata: os.stat_result) -> bool:
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _regular_release_file(relative: str) -> bool:
    if not _release_path(relative):
        return False
    current = ROOT
    for part in relative.split("/"):
        current = current / part
        try:
            metadata = current.lstat()
        except (FileNotFoundError, NotADirectoryError):
            return False
        if _linked(metadata):
            raise ValueError(
                f"Release path may not contain a link or junction: {relative}"
            )
    return stat.S_ISREG(metadata.st_mode)


def _fallback_candidates() -> set[str]:
    candidates = set()
    pending = [ROOT]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                relative = path.relative_to(ROOT).as_posix()
                if not _release_path(relative):
                    continue
                metadata = entry.stat(follow_symlinks=False)
                if _linked(metadata):
                    raise ValueError(
                        f"Release path may not contain a link or junction: {relative}"
                    )
                if stat.S_ISDIR(metadata.st_mode):
                    pending.append(path)
                elif stat.S_ISREG(metadata.st_mode):
                    candidates.add(relative)
    return candidates


def release_files() -> set[str]:
    try:
        repository = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if Path(repository).resolve() != ROOT:
            raise ValueError("This directory is not the repository root")
        raw = subprocess.check_output(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
        ).decode()
        candidates = {value for value in raw.split(chr(0)) if value}
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError, ValueError):
        candidates = _fallback_candidates()
    result = set()
    for relative in candidates:
        if not _release_path(relative):
            continue
        if _regular_release_file(relative):
            result.add(relative)
    return result


def write_manifest() -> None:
    if MANIFEST.exists() or MANIFEST.is_symlink():
        if _linked(MANIFEST.lstat()):
            raise ValueError("Manifest may not be a link or junction")
    entries = [
        f"{digest(ROOT / relative)}  {relative}" for relative in sorted(release_files())
    ]
    MANIFEST.write_text("\n".join(entries) + "\n", encoding="utf-8", newline="\n")
    print(f"MANIFEST written: {len(entries)} files")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify or regenerate the tracked release-file SHA-256 manifest"
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Regenerate MANIFEST.sha256 from release files",
    )
    args = parser.parse_args(argv)
    if args.write:
        write_manifest()
        return 0
    if not MANIFEST.exists():
        raise SystemExit("MANIFEST.sha256 is missing")
    if _linked(MANIFEST.lstat()):
        raise SystemExit("Manifest may not be a link or junction")
    expected = {}
    for n, line in enumerate(MANIFEST.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            d, rel = line.split("  ", 1)
        except ValueError as exc:
            raise SystemExit(f"Malformed manifest line {n}") from exc
        try:
            allowed = _release_path(rel)
        except ValueError as exc:
            raise SystemExit(f"Unsafe manifest line {n}: {exc}") from exc
        if not allowed or not re.fullmatch(r"[0-9a-f]{64}", d):
            raise SystemExit(f"Invalid release path or digest on manifest line {n}")
        if rel in expected:
            raise SystemExit(f"Duplicate manifest entry: {rel}")
        expected[rel] = d
    actual = release_files()
    errors = []
    for rel, d in expected.items():
        p = ROOT / rel
        if not _regular_release_file(rel):
            errors.append(f"missing: {rel}")
        elif digest(p) != d:
            errors.append(f"hash mismatch: {rel}")
    for rel in sorted(actual - set(expected)):
        errors.append(f"release file not in manifest: {rel}")
    for rel in sorted(set(expected) - actual):
        errors.append(f"manifest-only file: {rel}")
    if errors:
        raise SystemExit("MANIFEST verification failed:\n- " + "\n- ".join(errors))
    print(f"MANIFEST verified: {len(expected)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
