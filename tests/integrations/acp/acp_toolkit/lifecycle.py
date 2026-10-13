"""The OMP, Cursor and Copilot ACP adapters' bridge into the common Room
lifecycle suite (``tests/adapters/lifecycle``).

Each room process is a real stdio ACP agent (:mod:`workspacepeer`) started
through the adapter's production runtime; only the spawn is wrapped, to hold
its start and hold or refuse its exit.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from functools import partial
from typing import Any, Literal

import pytest
from acp import spawn_agent_process

from band.adapters.copilot_acp import CopilotACPAdapter, CopilotACPAdapterConfig
from band.adapters.cursor_acp import CursorACPAdapter, CursorACPAdapterConfig
from band.adapters.omp_acp import OmpACPAdapter, OmpACPAdapterConfig
from band.core.retention import RetainedRoom
from band.integrations.acp import client_adapter
from band.integrations.acp.client_adapter import ACPClientAdapter
from band.integrations.omp import OMP_ACP_SUBCOMMAND
from tests.adapters.hold import Hold
from tests.adapters.lifecycle.kit import LifecycleBridge, RetentionHost, pause_at
from tests.paths import REPO_ROOT

ACPProfile = Literal["omp_acp", "cursor_acp", "copilot_acp"]

PEER_MODULE = "tests.integrations.acp.acp_toolkit.workspacepeer"
# OMP's flag for the room workspace, which it gets instead of a launch cwd.
OMP_CWD_FLAG = "--cwd="
CLEANUP_FAILURE = "subprocess cleanup failed"


class LifecycleChild:
    """One room's ACP agent process, with the suite's held start and its held
    or refused exit injected at the spawn."""

    def __init__(self, spawn: AbstractAsyncContextManager[Any]) -> None:
        self.refuse_close = False
        self.starting: Hold | None = None
        self.closing: Hold | None = None
        self._spawn = spawn
        self._process: Any = None
        self._open = False

    @property
    def exited(self) -> bool:
        return self._process is not None and self._process.returncode is not None

    async def __aenter__(self) -> Any:
        connection, self._process = await self._spawn.__aenter__()
        self._open = True
        await pause_at(self.starting)
        return connection, self._process

    async def __aexit__(self, *exc_info: object) -> None:
        await pause_at(self.closing)
        if self.refuse_close:
            # The process keeps running, still owned by its room.
            raise RuntimeError(CLEANUP_FAILURE)
        await self.close()

    async def close(self) -> None:
        """Exit the spawn once, including one a refused exit left open."""
        if self._open:
            self._open = False
            await self._spawn.__aexit__(None, None, None)


def install(
    profile: ACPProfile, host: RetentionHost, monkeypatch: pytest.MonkeyPatch
) -> LifecycleBridge:
    """Build ``profile``'s adapter on its production config, every room process
    a :class:`LifecycleChild` handed to ``host``."""
    adapter = _adapter(profile, host)

    def spawn(client: Any, *command: str, cwd: str | None, **kwargs: Any) -> Any:
        child = LifecycleChild(spawn_agent_process(client, *command, cwd=cwd, **kwargs))
        host.spawned(child, _workspace(command, cwd))
        return child

    monkeypatch.setattr(client_adapter, "spawn_agent_process", spawn)
    return LifecycleBridge(
        adapter=adapter,
        residency=partial(_residency, adapter),
        native_id=partial(_native_id, adapter),
    )


def _adapter(profile: ACPProfile, host: RetentionHost) -> ACPClientAdapter[Any]:
    # The venv's launcher redirector would hide the peer's pid and exit on Windows.
    command = (sys._base_executable, "-m", PEER_MODULE, *host.peer_arguments)
    env = {"PYTHONPATH": str(REPO_ROOT), "__PYVENV_LAUNCHER__": sys.executable}
    match profile:
        case "omp_acp":
            return OmpACPAdapter(
                OmpACPAdapterConfig(command=(*command, OMP_ACP_SUBCOMMAND), env=env),
                workspace_for_room=host.workspace,
            )
        case "cursor_acp":
            return CursorACPAdapter(
                CursorACPAdapterConfig(command=command, env=env),
                workspace_for_room=host.workspace,
            )
        case "copilot_acp":
            return CopilotACPAdapter(
                CopilotACPAdapterConfig(command=command, env=env),
                workspace_for_room=host.workspace,
            )


def _workspace(command: Sequence[str], cwd: str | None) -> str | None:
    """The room workspace a process was started in."""
    if cwd is not None:
        return cwd
    flagged = (arg for arg in command if arg.startswith(OMP_CWD_FLAG))
    return next((arg.removeprefix(OMP_CWD_FLAG) for arg in flagged), None)


def _residency(adapter: ACPClientAdapter[Any], room_id: str) -> RetainedRoom | None:
    room = adapter._rooms.get(room_id)
    return None if room is None else room.retained


def _native_id(adapter: ACPClientAdapter[Any], room_id: str) -> str | None:
    room = adapter._rooms.get(room_id)
    return None if room is None or room.session is None else room.session.session_id
