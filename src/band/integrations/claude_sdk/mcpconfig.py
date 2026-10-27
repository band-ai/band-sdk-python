"""Private ``--mcp-config`` files for Claude CLI starts.

The SDK serializes an ``mcp_servers`` dict into the CLI's argv, which other OS
users can read from the process list; the Band MCP server's bearer token
travels in those servers' headers. Given a path instead, the SDK passes only
the path, so the token stays in a file only this OS user can open.
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
# The CLI's ``--mcp-config`` file shape.
MCP_SERVERS_KEY = "mcpServers"
_PRIVATE_FILE_MODE = 0o600


class McpConfigFiles:
    """One ``<room_id>.json`` per room in a directory private to this user.

    The directory is created on the first write (``mkdtemp``: mode ``0700`` on
    POSIX; on Windows under the user's own temp directory) and removed by
    ``remove()``. A room's file is rewritten for each CLI start; a room starts
    its next CLI only once the previous one has closed.
    """

    def __init__(self) -> None:
        self._directory: Path | None = None

    def write(self, room_id: str, servers: Mapping[str, Any]) -> Path:
        """Write ``room_id``'s MCP servers; return the file's path."""
        path = self._ensure_directory() / f"{quote(room_id, safe='')}.json"
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _PRIVATE_FILE_MODE
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump({MCP_SERVERS_KEY: dict(servers)}, file)
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
