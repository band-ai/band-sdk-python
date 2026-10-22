"""Durable, exclusive file publication shared by core artifacts and host output."""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

from remote_agent.capabilities import require_linux
from remote_agent.storage import StoreError, private_directory, sync_directory
from remote_agent.types import SourcePath


def publish_file(
    destination: SourcePath,
    write: Callable[[BinaryIO], object],
    *,
    exist_ok: bool = False,
) -> None:
    destination = Path(destination).absolute()
    directory = private_directory(destination.parent)
    destination = directory / destination.name
    if destination.exists() or destination.is_symlink():
        if exist_ok:
            return
        raise StoreError("Destination already exists")
    descriptor, temporary = tempfile.mkstemp(prefix=".journal-export-", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            write(stream)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if not exist_ok:
                raise
        else:
            Path(temporary).unlink(missing_ok=True)
            sync_directory(directory)
    except OSError as error:
        raise StoreError("Cannot publish operator output") from error
    finally:
        Path(temporary).unlink(missing_ok=True)


def recover_staging(path: SourcePath) -> None:
    # A death after linking a complete, synced file can leave its staging name.
    # Remove only names for this exact owned inode. Callers must still validate
    # the final file and reject any unrelated aliases before reading its bytes.
    require_linux()
    path = Path(path)
    info = path.lstat()
    if (
        stat.S_ISREG(info.st_mode)
        and info.st_uid == os.getuid()
        and not info.st_mode & 0o077
        and info.st_nlink > 1
    ):
        removed = False
        for staging in path.parent.glob(".journal-export-*"):
            try:
                other = staging.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISREG(other.st_mode) and (other.st_dev, other.st_ino) == (
                info.st_dev,
                info.st_ino,
            ):
                try:
                    staging.unlink()
                except FileNotFoundError:
                    continue
                removed = True
        if removed:
            sync_directory(path.parent)
