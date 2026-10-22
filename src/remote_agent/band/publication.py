"""Publish immutable startup pins only after their complete bytes are durable."""

from __future__ import annotations

from pathlib import Path

from remote_agent.publication import publish_file, recover_staging
from remote_agent.service import read_file


def read_published(path: Path, *, maximum: int = 65536) -> bytes:
    recover_staging(path)
    return read_file(path, maximum=maximum)


def immutable_bytes(path: Path, encoded: bytes, *, conflict: str) -> None:
    if path.exists() or path.is_symlink():
        if read_published(path, maximum=max(524288, len(encoded))) != encoded:
            raise ValueError(conflict)
        return
    publish_file(path, lambda stream: stream.write(encoded))
