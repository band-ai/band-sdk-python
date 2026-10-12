"""A real Python child behind the SDK's subprocess transport."""

from __future__ import annotations

import sys

from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

from band.runtime.tools import BAND_MCP_SERVER_NAME
from tests.paths import REPO_ROOT

PROBE = """
import json
import sys
from pathlib import Path

Path("probe.txt").write_text(sys.argv[1])
sys.stdout.write(json.dumps({"cwd": str(Path.cwd()), "marker": sys.argv[1]}) + chr(10))
sys.stdout.flush()
sys.stdin.read()
"""


class WorkspaceProcess(SubprocessCLITransport):
    """Exercise SDK spawning, pipes and cleanup without a Claude installation."""

    refuse_close = False

    async def close(self) -> None:
        if self.refuse_close:
            raise RuntimeError("subprocess cleanup failed")
        await super().close()

    async def _check_claude_version(self) -> None:
        pass

    def _build_command(self) -> list[str]:
        return [sys.executable, "-u", "-c", PROBE, self._options.env["PROBE_MARKER"]]

    @property
    def exited(self) -> bool:
        return self._child.returncode is not None

    async def connect(self) -> None:
        await super().connect()
        assert self._process is not None
        self._child = self._process


class WorkspacePeer(WorkspaceProcess):
    """A child that speaks stream-json and executes relative file operations."""

    arguments: tuple[str, ...] = ()

    def _build_command(self) -> list[str]:
        command = [
            sys.executable,
            "-u",
            str(REPO_ROOT / "tests/adapters/roompeer.py"),
            *self.arguments,
        ]
        if server := self._options.mcp_servers.get(BAND_MCP_SERVER_NAME):
            command.extend(["--band-url", str(server["url"])])
        return command
