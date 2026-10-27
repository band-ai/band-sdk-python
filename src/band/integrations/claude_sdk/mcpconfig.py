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
    POSIX; on Windows under the user's own temp directory) and removed by
    ``remove()``. A room's file is replaced whole for each CLI start, so a
    failed write never leaves a partial file behind.
    """

    def __init__(self) -> None:
        self._directory: Path | None = None

    def write(self, room_id: str, servers: Mapping[str, Any]) -> Path:
        """Write ``room_id``'s MCP servers; return the file's path."""
        directory = self._ensure_directory()
        path = directory / f"{quote(room_id, safe='')}.json"
        # mkstemp creates the file readable and writable by this user only.
        descriptor, staged = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                json.dump({MCP_SERVERS_KEY: dict(servers)}, file)
            os.replace(staged, path)
        except BaseException:
            Path(staged).unlink(missing_ok=True)
            raise
        return path

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

    def _ensure_directory(self) -> Path:
        if self._directory is None:
            self._directory = Path(tempfile.mkdtemp(prefix=MCP_CONFIG_DIR_PREFIX))
        return self._directory
