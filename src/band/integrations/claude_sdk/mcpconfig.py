"""Private ``--mcp-config`` files for Claude CLI starts.

The SDK serializes an ``mcp_servers`` dict into the CLI's argv, which other OS
users can read from the process list; the Band MCP server's bearer token
travels in those servers' headers. Servers the CLI dials itself therefore go
to a file only this OS user can open, passed as a second ``--mcp-config``
(the CLI merges every one it is given). In-process ``sdk`` servers stay in
the dict: the SDK routes their calls only when it finds them there, and their
argv entry carries no secret.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

logger = logging.getLogger(__name__)

MCP_CONFIG_DIR_PREFIX = "band-claude-mcp-"
# The CLI flag, as an ``extra_args`` key, naming an MCP config file.
MCP_CONFIG_FLAG = "mcp-config"
# The CLI's ``--mcp-config`` file shape.
MCP_SERVERS_KEY = "mcpServers"
# The ``type`` of an in-process server the SDK itself serves.
SDK_SERVER_TYPE = "sdk"
REMOVAL_FAILED_LOG = "Could not remove Claude MCP config directory %s"
DISCARD_FAILED_LOG = "Could not delete Claude MCP config file %s"
REPLACED_DIRECTORY_LOG = (
    "Claude MCP config directory %s is no longer the one this process "
    "created; writing to a new one"
)


def is_in_process(config: object) -> bool:
    """Whether an ``mcp_servers`` entry is an in-process ``sdk`` server."""
    return isinstance(config, Mapping) and config.get("type") == SDK_SERVER_TYPE


class McpConfigFiles:
    """One ``<room_id>.json`` per room in a directory private to this user.

    The directory is created on the first write (``mkdtemp``: mode ``0700`` on
    POSIX; on Windows under the user's own temp directory) and reused only
    while it is still that directory (a temp reaper may remove it while the
    agent idles, and something else may then take its name); ``remove()``
    deletes it and ``discard()`` a disposed room's file. A room's file is
    replaced whole for each CLI start, so a failed write never leaves a
    partial file behind.
    """

    def __init__(self) -> None:
        self._directory: Path | None = None
        self._identity: DirectoryIdentity | None = None

    def write(self, room_id: str, servers: Mapping[str, Any]) -> Path:
        """Write ``room_id``'s MCP servers; return the file's path."""
        path = self._ensure_directory() / self._file_name(room_id)
        # mkstemp creates the file readable and writable by this user only.
        descriptor, staged = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                json.dump({MCP_SERVERS_KEY: dict(servers)}, file)
            os.replace(staged, path)
        except BaseException:
            Path(staged).unlink(missing_ok=True)
            raise
        return path

    def discard(self, room_id: str) -> None:
        """Delete ``room_id``'s file, if one was written; one that cannot be
        deleted now goes with ``remove()``."""
        if self._directory is None:
            return
        path = self._directory / self._file_name(room_id)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning(DISCARD_FAILED_LOG, path, exc_info=True)

    def remove(self) -> None:
        """Delete the directory and every file in it."""
        if self._directory is None:
            return
        try:
            shutil.rmtree(self._directory)
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning(REMOVAL_FAILED_LOG, self._directory, exc_info=True)
            return
        self._directory = None

    @staticmethod
    def _file_name(room_id: str) -> str:
        return f"{quote(room_id, safe='')}.json"

    def _ensure_directory(self) -> Path:
        directory = self._directory
        if directory is not None and _identity(directory) == self._identity:
            return directory
        if directory is not None and os.path.lexists(directory):
            logger.warning(REPLACED_DIRECTORY_LOG, directory)
        created = Path(tempfile.mkdtemp(prefix=MCP_CONFIG_DIR_PREFIX))
        self._directory, self._identity = created, _identity(created)
        return created


# A directory's device, inode and owner.
DirectoryIdentity = tuple[int, int, int]


def _identity(path: Path) -> DirectoryIdentity | None:
    """``path``'s identity while it is a directory itself, not a link to one."""
    try:
        status = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(status.st_mode):
        return None
    return status.st_dev, status.st_ino, status.st_uid
