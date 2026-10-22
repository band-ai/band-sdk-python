"""Own canonical journal bytes and private filesystem boundaries."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

from remote_agent.capabilities import require_linux
from remote_agent.types import SourcePath


class StoreError(Exception):
    pass


def canonical(value: object) -> bytes:
    def validate(item: object) -> None:
        if item is None or isinstance(item, (str, bool)):
            return
        if type(item) is int and abs(item) <= 9_007_199_254_740_991:
            return
        if isinstance(item, list):
            for child in item:
                validate(child)
            return
        if isinstance(item, dict) and all(isinstance(key, str) for key in item):
            for child in item.values():
                validate(child)
            return
        raise StoreError("Expected JSON with string keys and safe integer numbers")

    try:
        validate(value)
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    except (UnicodeError, RecursionError) as error:
        raise StoreError("Invalid JSON encoding or nesting") from error


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def private_directory(path: SourcePath) -> Path:
    require_linux()
    path = Path(path).resolve()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise StoreError(
            "Storage directory must be owned by this user and not writable by others"
        )
    return path


def private_file(path: SourcePath, *, create: bool = True) -> int:
    """Never follow a final symlink or allow hard-link aliases to ownership locks."""
    require_linux()
    flags = os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
    if create:
        flags |= os.O_CREAT
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as error:
        raise StoreError("Cannot open private storage file") from error
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
        ):
            raise StoreError(
                "Storage must be a single-link regular file owned by this user"
            )
        os.fchmod(descriptor, 0o600)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def sync_directory(path: SourcePath) -> None:
    require_linux()
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
