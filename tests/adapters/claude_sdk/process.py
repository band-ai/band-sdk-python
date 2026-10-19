"""A real Python child behind the SDK's subprocess transport."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TypeVar

import pytest
from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

from band.core.exceptions import CleanupIncompleteError
from band.integrations.claude_sdk import transport
from band.runtime.tools import BAND_MCP_SERVER_NAME
from tests.adapters.roompeer import CLEANUP_FAILURE
from tests.paths import ROOM_PEER

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
    # Closes refused before one succeeds; ``refuse_close`` refuses every one.
    refused_closes = 0

    async def close(self) -> None:
        if self.refuse_close or self.refused_closes:
            self.refused_closes = max(self.refused_closes - 1, 0)
            raise RuntimeError(CLEANUP_FAILURE)
        await super().close()

    async def _check_claude_version(self) -> None:
        pass

    def _build_command(self) -> list[str]:
        return [sys.executable, "-u", "-c", PROBE, self._options.env["PROBE_MARKER"]]

    @property
    def workspace(self) -> str | None:
        """The directory the SDK starts this child in, if the options set one."""
        cwd = self._options.cwd
        return str(cwd) if cwd else None

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
            str(ROOM_PEER),
            *self.arguments,
        ]
        if server := self._options.mcp_servers.get(BAND_MCP_SERVER_NAME):
            command.extend(["--band-url", str(server["url"])])
        return command


Child = TypeVar("Child", bound=SubprocessCLITransport)


def launch_children(
    monkeypatch: pytest.MonkeyPatch,
    child_type: type[Child],
    on_launch: Callable[[Child], None] | None = None,
) -> list[Child]:
    """Start every CLI the SDK launches as a real ``child_type`` process;
    ``on_launch`` prepares each one before it starts. Returns the children,
    in launch order, as they are launched."""
    children: list[Child] = []

    def launch(*, prompt: str, options: ClaudeAgentOptions) -> Child:
        child = child_type(prompt=prompt, options=options)
        if on_launch is not None:
            on_launch(child)
        children.append(child)
        return child

    monkeypatch.setattr(transport, "SubprocessCLITransport", launch)
    return children


class ForgetfulProcess(WorkspaceProcess):
    """A native close that returns while its child still runs and forgets it."""

    async def close(self) -> None:
        self._process = None


class DelegatingProcess(ForgetfulProcess):
    """A native close that requires a physical-exit custodian before returning."""

    _exit_confirmation_deferred = False

    def _defer_exit_confirmation(self) -> None:
        self._exit_confirmation_deferred = True

    async def close(self) -> None:
        if not self._exit_confirmation_deferred:
            raise CleanupIncompleteError("Claude CLI process exit is unconfirmed")
        await super().close()
