"""Confined file operations. Fallback platforms require trusted local directories."""

from __future__ import annotations

import errno
import os
import stat
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException


def relative_parts(value: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 1024:
        raise HTTPException(400, "Invalid relative file path")
    parts = tuple(value.split("/"))
    if len(parts) > 32 or any(
        part in {"", ".", ".."}
        or part.endswith((".", " "))
        or any(char in '\\:<>|?*"' or ord(char) < 32 or ord(char) == 127 for char in part)
        or part.split(".", 1)[0].upper()
        in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
        for part in parts
    ):
        raise HTTPException(400, "Invalid relative file path")
    return parts


def descriptor_paths_supported() -> bool:
    # Python lists rename rather than replace in supports_dir_fd.
    return (
        hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
        and all(fn in os.supports_dir_fd for fn in (os.open, os.mkdir, os.stat, os.unlink, os.rename, os.link))
    )


def check_leaf(parent: Path, name: str, fd: int | None) -> os.stat_result | None:
    try:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False) if fd is not None else (parent / name).lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise HTTPException(400, "File must be regular and may not be a symbolic link")
    return info


@contextmanager
def confined_parent(root: Path, relative: str, *, create: bool = False):
    parts = relative_parts(relative)
    parent = root.joinpath(*parts[:-1])
    fd = None
    try:
        if descriptor_paths_supported():
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            fd = os.open(root, flags)
            for part in parts[:-1]:
                if create:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                child = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = child
        else:
            current = root
            for part in parts[:-1]:
                current = current / part
                if create:
                    current.mkdir(mode=0o700, exist_ok=True)
                info = current.lstat()
                if not stat.S_ISDIR(info.st_mode) or current.is_symlink() or current.resolve() != current:
                    raise HTTPException(400, "File parent must be a real directory")
        check_leaf(parent, parts[-1], fd)
        yield parent, parts[-1], fd
    except FileNotFoundError as exc:
        raise HTTPException(404, "File or parent directory was not found") from exc
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise HTTPException(400, "Symbolic links and non-directory file parents are forbidden") from exc
        raise
    finally:
        if fd is not None:
            os.close(fd)
