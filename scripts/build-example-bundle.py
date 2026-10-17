#!/usr/bin/env python3
"""Create the deterministic, copy-ready exampleApps release ZIP."""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
import subprocess
import tempfile
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE_DOCUMENTS = (
    "LICENSE",
    "LICENSE-FAQ.md",
    "NOTICE.md",
    "THIRD_PARTY_NOTICES.md",
    "TRADEMARK_POLICY.md",
    "AI_USAGE_POLICY.md",
    "LICENSE-HISTORY.md",
    "LICENSE-METADATA.json",
    "README.md",
    "VERSION",
    "CONTRIBUTING.md",
    "CONTRIBUTOR_LICENSE_AGREEMENT.md",
    "SECURITY.md",
    "GOVERNANCE.md",
    "OWNERSHIP.md",
)
MAX_FILES = 10_000
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_FILE_BYTES = 16 * 1024 * 1024


def _portable_name(name: str) -> str:
    """Use names that stay distinct when extracted on supported filesystems."""
    devices = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    devices.update(
        f"{prefix}{suffix}" for prefix in ("COM", "LPT") for suffix in "123456789¹²³"
    )
    for part in name.split("/"):
        if (
            not part
            or part in {".", ".."}
            or part.endswith((".", " "))
            or any(
                char in '<>:"\\|?*' or ord(char) < 32 or ord(char) == 127
                for char in part
            )
            or part.split(".", 1)[0].rstrip(" ").upper() in devices
        ):
            raise RuntimeError(f"bundle source has an unsafe filename: {name!r}")
    return unicodedata.normalize("NFC", name).casefold()


def _same_location(left: Path, right: Path) -> bool:
    # Case and Unicode aliases must be refused even on a case-sensitive build
    # host. samefile also covers actual filesystem aliases such as short names.
    def key(path: Path) -> str:
        return unicodedata.normalize("NFC", str(path)).casefold()

    if key(left) == key(right):
        return True
    try:
        return left.samefile(right)
    except FileNotFoundError:
        return False


def _check_delivery_path(path: Path) -> None:
    """Fail closed for local state even when Git/manifest checks ignore it."""
    _portable_name(path.relative_to(ROOT).as_posix())
    parts = tuple(part.casefold() for part in path.relative_to(ROOT).parts)
    generated = {
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".venv",
        "venv",
        "node_modules",
        "build",
        "dist",
        "htmlcov",
    }
    name = parts[-1]
    if (
        any(part in generated or part.endswith(".egg-info") for part in parts)
        or (name.startswith(".env") and name != ".env.example")
        or name.endswith(
            (
                ".pem",
                ".key",
                ".p12",
                ".pfx",
                ".db",
                ".sqlite",
                ".sqlite3",
                ".log",
                ".pyc",
                ".pyo",
            )
        )
        or any(marker in name for marker in (".db-", ".sqlite-", ".sqlite3-"))
    ):
        raise RuntimeError(f"bundle source contains private or generated state: {path}")


def _is_link(metadata: os.stat_result) -> bool:
    # Windows junctions are reparse points but are not reported as symlinks.
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _source_date_epoch() -> int:
    configured = os.environ.get("SOURCE_DATE_EPOCH")
    if configured:
        return int(configured)
    try:
        return int(
            subprocess.check_output(
                ["git", "log", "-1", "--pretty=%ct"],
                cwd=ROOT,
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return 315_532_800


def _sources() -> list[tuple[Path, str, os.stat_result]]:
    app_root = ROOT / "app"
    if _is_link(app_root.lstat()) or not app_root.is_dir():
        raise RuntimeError("app root must be a real directory")
    paths = []
    for count, path in enumerate(app_root.rglob("*"), 1):
        if count > MAX_FILES * 2:
            raise RuntimeError("bundle source contains too many directory entries")
        _check_delivery_path(path)
        if _is_link(path.lstat()):
            raise RuntimeError(f"bundle source contains symlinks or junctions: {path}")
        if path.is_file():
            paths.append(path)
    paths.extend(
        [
            *(ROOT / name for name in BUNDLE_DOCUMENTS),
            ROOT / "EXAMPLE_APPS.md",
            ROOT / "scripts/install-example.sh",
            ROOT / "scripts/install-example.ps1",
            ROOT / "scripts/taskboard_client.py",
        ]
    )
    if len(paths) > MAX_FILES:
        raise RuntimeError(f"bundle file count exceeds {MAX_FILES}")

    result: list[tuple[Path, str, os.stat_result]] = []
    names = set()
    components: dict[str, str] = {}
    total = 0
    for path in paths:
        _check_delivery_path(path)
        for parent in path.parents:
            if parent == ROOT:
                break
            if _is_link(parent.lstat()):
                raise RuntimeError(
                    f"bundle source parent contains symlinks or junctions: {parent}"
                )
        metadata = path.lstat()
        if _is_link(metadata) or not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError(
                f"bundle source must be a regular non-symlink file: {path}"
            )
        if metadata.st_size > MAX_FILE_BYTES:
            raise RuntimeError(f"bundle file exceeds {MAX_FILE_BYTES} bytes: {path}")
        total += metadata.st_size
        if total > MAX_TOTAL_BYTES:
            raise RuntimeError(f"bundle content exceeds {MAX_TOTAL_BYTES} bytes")
        name = path.relative_to(ROOT).as_posix()
        key = _portable_name(name)
        if key in names:
            raise RuntimeError(
                f"bundle source has an unsafe or colliding filename: {name!r}"
            )
        parts = name.split("/")
        for count in range(1, len(parts) + 1):
            prefix = "/".join(parts[:count])
            alias = unicodedata.normalize("NFC", prefix).casefold()
            if alias in components and components[alias] != prefix:
                raise RuntimeError(
                    f"bundle source has a colliding path component: {prefix!r}"
                )
            components[alias] = prefix
        names.add(key)
        result.append((path, name, metadata))
    return sorted(result, key=lambda item: item[1])


def build(output: Path) -> None:
    output = output.resolve()
    app_root = (ROOT / "app").resolve()
    if any(_same_location(parent, app_root) for parent in (output, *output.parents)):
        raise RuntimeError("bundle output must not be inside app sources")
    sources = _sources()
    if any(_same_location(output, path.resolve()) for path, _, _ in sources):
        raise RuntimeError("bundle output must not replace a delivery source")
    output.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.fromtimestamp(
        min(max(_source_date_epoch(), 315_532_800), 4_354_819_198), tz=timezone.utc
    ).timetuple()[:6]
    checksums: list[str] = []

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    temporary = Path(temporary_name)
    try:
        with (
            os.fdopen(fd, "wb") as destination,
            zipfile.ZipFile(
                destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
            ) as archive,
        ):
            for path, name, expected in sources:
                info = zipfile.ZipInfo(name, timestamp)
                info.create_system = 3
                mode = (
                    0o100755
                    if name == "scripts/install-example.sh" or expected.st_mode & 0o111
                    else 0o100644
                )
                info.external_attr = mode << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                digest = hashlib.sha256()
                source_fd = os.open(
                    path,
                    os.O_RDONLY
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_BINARY", 0),
                )
                with os.fdopen(source_fd, "rb") as source:
                    before = os.fstat(source.fileno())

                    def identity(value):
                        return (
                            value.st_dev,
                            value.st_ino,
                            value.st_size,
                            value.st_mtime_ns,
                            # Windows ctime can change when opening a file even if
                            # its contents did not change. File ID, size and mtime
                            # remain comparable between lstat and fstat there.
                            value.st_ctime_ns if os.name != "nt" else None,
                        )

                    if not stat.S_ISREG(before.st_mode) or identity(before) != identity(
                        expected
                    ):
                        raise RuntimeError(
                            f"bundle source changed before reading: {name}"
                        )
                    size = 0
                    with archive.open(info, "w") as member:
                        while chunk := source.read(1024 * 1024):
                            size += len(chunk)
                            if size > expected.st_size or size > MAX_FILE_BYTES:
                                raise RuntimeError(
                                    f"bundle source grew while reading: {name}"
                                )
                            digest.update(chunk)
                            member.write(chunk)
                    if size != expected.st_size or identity(
                        os.fstat(source.fileno())
                    ) != identity(before):
                        raise RuntimeError(
                            f"bundle source changed while reading: {name}"
                        )
                checksums.append(f"{digest.hexdigest()}  {name}")

            checksum_content = ("\n".join(checksums) + "\n").encode()
            info = zipfile.ZipInfo("SHA256SUMS", timestamp)
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, checksum_content, compresslevel=9)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Wrote {output} with {len(sources)} source files")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
