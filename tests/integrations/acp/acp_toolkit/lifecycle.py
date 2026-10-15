"""The Cursor and Copilot ACP adapters' bridge into the common Room
lifecycle suite (``tests/adapters/lifecycle``).

Each room process is a real stdio ACP agent (:mod:`workspacepeer`) started
through the adapter's production runtime; only the spawn is wrapped, to hold
its start and hold or refuse its exit.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import AbstractAsyncContextManager
from functools import partial
from typing import Any, Literal, Self

import pytest
from acp import spawn_agent_process

from band.adapters.copilot_acp import CopilotACPAdapter, CopilotACPAdapterConfig
from band.adapters.cursor_acp import CursorACPAdapter, CursorACPAdapterConfig
from band.core.retention import RetainedRoom
from band.integrations.acp import client_adapter, client_runtime
from band.integrations.acp.client_adapter import ACPClientAdapter
from tests.adapters.hold import Hold
from tests.adapters.lifecycle.kit import LifecycleBridge, RetentionHost, pause_at
from tests.adapters.roompeer import CLEANUP_FAILURE
from tests.paths import REPO_ROOT

ACPProfile = Literal["cursor_acp", "copilot_acp"]

PEER_MODULE = "tests.integrations.acp.acp_toolkit.workspacepeer"


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
    def process(self) -> Any:
        return self._process

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


class FailingExits:
    """``spawn_agent_process`` whose every exit is refused, leaving the agent
    running as a failed teardown does; leaving it closes them all."""

    def __init__(self) -> None:
        self.children: list[LifecycleChild] = []

    def __call__(self, *args: Any, **kwargs: Any) -> LifecycleChild:
        child = LifecycleChild(spawn_agent_process(*args, **kwargs))
        child.refuse_close = True
        self.children.append(child)
        return child

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        for child in self.children:
            await child.close()


def install(
    profile: ACPProfile, host: RetentionHost, monkeypatch: pytest.MonkeyPatch
) -> LifecycleBridge:
    """Build ``profile``'s adapter on its production config, every room process
    a :class:`LifecycleChild` handed to ``host``."""
    adapter = _adapter(profile, host)

    def spawn(client: Any, *command: str, cwd: str | None, **kwargs: Any) -> Any:
        child = LifecycleChild(spawn_agent_process(client, *command, cwd=cwd, **kwargs))
        host.spawned(child, cwd)
        return child

    kill_process = client_runtime._kill

    async def kill(process: asyncio.subprocess.Process) -> None:
        child = next(
            child
            for child in host.children
            if isinstance(child, LifecycleChild) and child.process is process
        )
        await pause_at(child.closing)
        await kill_process(process)

    monkeypatch.setattr(client_adapter, "spawn_agent_process", spawn)
    monkeypatch.setattr(client_runtime, "_kill", kill)
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


def _residency(adapter: ACPClientAdapter[Any], room_id: str) -> RetainedRoom | None:
    room = adapter._rooms.get(room_id)
    return None if room is None else room.retained


def _native_id(adapter: ACPClientAdapter[Any], room_id: str) -> str | None:
    room = adapter._rooms.get(room_id)
    return None if room is None or room.session is None else room.session.session_id
