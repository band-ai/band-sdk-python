"""Gated native callbacks and activity observation for OMP turn tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from band_rest.types.agent_activity_detail import AgentActivityDetail

from band.adapters.omp import OmpAdapter, OmpAdapterConfig, OmpTurn, PermissionResolver
from band.converters.omp import OmpSessionState
from band.core.types import Emit
from band.integrations.ompwire import (
    OmpEvent,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
)
from band.runtime.activity import TurnActivity
from band.runtime.tools import AgentTools
from band.testing.activity import ActivityRecorder
from band.testing.fake_tools import FakeAgentTools
from tests.adapters.omp.kit import (
    EMIT_OFF,
    EMIT_ON,
    GatedPermissionResolver,
    RecordingRuntime,
    make_adapter,
    permission_request,
)
from tests.integrations.acp.conftest import make_platform_message


class TurnActivityRecorder(ActivityRecorder):
    """Observe visible detail through the real turn activity reducer."""

    def __init__(self) -> None:
        super().__init__()
        self.activity = TurnActivity()

    async def update(self, activity: AgentActivityDetail) -> None:
        self.activity.apply(activity=activity)
        await super().update(activity)

    async def set_manual_wait(self, waiting: bool) -> None:
        self.activity.apply(waiting=waiting)
        await super().set_manual_wait(waiting)


class ActivityRuntime(RecordingRuntime):
    """Hold a prompt open while the test drives its native callbacks."""

    def __init__(self) -> None:
        super().__init__()
        self.ready = asyncio.Event()
        self.on_event: Callable[[OmpEvent], Awaitable[None]]
        self.on_tool: Callable[[OmpHostToolCall], Awaitable[OmpToolResult]]
        self.on_ui: Callable[[OmpUIRequest], Awaitable[OmpUIResponse]]

    async def prompt(
        self,
        _message: str,
        *,
        on_event: Callable[[OmpEvent], Awaitable[None]],
        on_tool: Callable[[OmpHostToolCall], Awaitable[OmpToolResult]],
        on_ui: Callable[[OmpUIRequest], Awaitable[OmpUIResponse]],
    ) -> None:
        self.on_event, self.on_tool, self.on_ui = on_event, on_tool, on_ui
        self.ready.set()
        await self.finish.wait()


@dataclass
class ActivityTurn:
    runtime: ActivityRuntime
    recorder: TurnActivityRecorder
    tools: AgentTools
    running: asyncio.Task[None]

    async def finish(self) -> None:
        self.runtime.finish.set()
        await self.running


@asynccontextmanager
async def activity_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    rest_client: MagicMock,
    *,
    emit: tuple[Emit, ...] = EMIT_ON,
    resolve_permission: PermissionResolver | None = None,
) -> AsyncIterator[ActivityTurn]:
    runtime = ActivityRuntime()
    monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
    recorder = TurnActivityRecorder()
    tools = recorder.observing(AgentTools("room", rest_client))
    adapter = OmpAdapter(
        OmpAdapterConfig(cwd=str(tmp_path)),
        emit=emit,
        resolve_permission=resolve_permission,
    )
    running = asyncio.create_task(
        adapter.on_message(
            msg=make_platform_message("Read and find peers"),
            tools=tools,
            history=OmpSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room",
        )
    )
    try:
        await runtime.ready.wait()
        yield ActivityTurn(runtime, recorder, tools, running)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await adapter.cleanup_all()


@dataclass
class ConcurrentPermissions:
    recorder: ActivityRecorder
    resolver: GatedPermissionResolver
    first: asyncio.Task[OmpUIResponse]
    second: asyncio.Task[OmpUIResponse]


@asynccontextmanager
async def concurrent_permissions(
    tmp_path: Path,
) -> AsyncIterator[ConcurrentPermissions]:
    recorder = ActivityRecorder()
    turn = OmpTurn(tools=recorder.observing(FakeAgentTools()))
    resolver = GatedPermissionResolver("first", "second")
    adapter = make_adapter(tmp_path, EMIT_OFF, resolve_permission=resolver)
    first = asyncio.create_task(
        adapter._permission(permission_request("first"), "room", "sess", turn)
    )
    second = asyncio.create_task(
        adapter._permission(permission_request("second"), "room", "sess", turn)
    )
    try:
        await resolver.entered["first"].wait()
        await resolver.entered["second"].wait()
        yield ConcurrentPermissions(recorder, resolver, first, second)
    finally:
        first.cancel()
        second.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
