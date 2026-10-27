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


def is_in_process(config: object) -> bool:
    """Whether an ``mcp_servers`` entry is an in-process ``sdk`` server."""
    return isinstance(config, Mapping) and config.get("type") == SDK_SERVER_TYPE


class McpConfigFiles:
    """One ``<room_id>.json`` per room in a directory private to this user.

    The directory is created on the first write (``mkdtemp``: mode ``0700`` on
    POSIX; on Windows under the user's own temp directory), created afresh
    once it is no longer private (a temp reaper removed it while the agent
    idled, and another user may have taken its name), and removed by
    ``remove()``; ``discard()`` deletes a disposed room's file. A room's file
    is replaced whole for each CLI start, so a failed write never leaves a
    partial file behind.
    """

    def __init__(self) -> None:
        self._directory: Path | None = None

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
        """Delete ``room_id``'s file, if one was written."""
        if self._directory is not None:
            (self._directory / self._file_name(room_id)).unlink(missing_ok=True)

    def remove(self) -> None:
        """Delete the directory and every file in it."""
        if self._directory is None:
            return
        try:
            shutil.rmtree(self._directory)
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning(
                "Could not remove Claude MCP config directory %s",
                self._directory,
                exc_info=True,
            )
            return
        self._directory = None

    @staticmethod
    def _file_name(room_id: str) -> str:
        return f"{quote(room_id, safe='')}.json"

    def _ensure_directory(self) -> Path:
        if self._directory is None or not _is_private_directory(self._directory):
            self._directory = Path(tempfile.mkdtemp(prefix=MCP_CONFIG_DIR_PREFIX))
        return self._directory


def _is_private_directory(path: Path) -> bool:
    """Whether ``path`` is a directory only this user can open, as ``mkdtemp``
    made it."""
    try:
        status = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(status.st_mode):
        return False
    if os.name == "nt":
        # The user's own temp directory, which other users cannot write.
        return True
    return status.st_uid == os.getuid() and stat.S_IMODE(status.st_mode) == 0o700
