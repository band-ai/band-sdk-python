"""Gated native callbacks and activity observation for OMP turn tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from band.adapters.omp import (
    OmpAdapter,
    OmpAdapterConfig,
    OmpPermissionRequest,
)
from band.adapters.omp.config import PermissionResolver
from band.adapters.omp.turnstate import OmpTurn
from band.converters.omp import OmpSessionState
from band.core.types import Emit
from band.integrations.omp import OMP_FORM_APPROVE, OMP_FORM_DENY
from band.integrations.ompwire import (
    OmpContent,
    OmpContentKind,
    OmpEvent,
    OmpEventType,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIMethod,
    OmpUIRequest,
    OmpUIResponse,
)
from band.runtime.tools import AgentTools
from band.testing.activity import ActivityRecorder
from band.testing.fake_tools import FakeAgentTools
from tests.adapters.omp.models import RecordingRuntime
from tests.integrations.acp.conftest import make_platform_message

EMIT_ON: tuple[Emit, ...] = (Emit.TOOL_CALLS,)


EMIT_OFF: tuple[Emit, ...] = ()


def make_adapter(
    tmp_path: Path,
    emit: tuple[Emit, ...],
    resolve_permission: PermissionResolver | None = None,
) -> OmpAdapter:
    return OmpAdapter(
        OmpAdapterConfig(cwd=str(tmp_path)),
        emit=emit,
        resolve_permission=resolve_permission,
    )


class GatedPermissionResolver:
    """Hold each manual choice until its test supplies a result."""

    def __init__(self, *titles: str) -> None:
        self.entered = {title: asyncio.Event() for title in titles}
        self.responses: dict[str, asyncio.Future[str | None]] = {
            title: asyncio.get_running_loop().create_future() for title in titles
        }

    async def __call__(self, request: OmpPermissionRequest) -> str | None:
        self.entered[request.title].set()
        return await self.responses[request.title]


def permission_request(request_id: str) -> OmpUIRequest:
    return OmpUIRequest(
        id=request_id,
        method=OmpUIMethod.SELECT,
        title=request_id,
        options=[OMP_FORM_APPROVE, OMP_FORM_DENY],
    )


def tool_start(call_id: str, name: str, **args: object) -> OmpEvent:
    return OmpEvent(
        type=OmpEventType.TOOL_START,
        toolCallId=call_id,
        toolName=name,
        args=args,
    )


def tool_end(call_id: str, name: str, *, is_error: bool = False) -> OmpEvent:
    return OmpEvent(
        type=OmpEventType.TOOL_END,
        toolCallId=call_id,
        toolName=name,
        result=OmpToolResult(
            content=[OmpContent(type=OmpContentKind.TEXT, text="private-result")]
        ),
        isError=is_error,
    )


class ActivityRuntime(RecordingRuntime):
    """Hold a prompt open while the test drives its native callbacks."""

    def __init__(self) -> None:
        super().__init__()
        self.ready = asyncio.Event()
        self.on_event: Callable[[OmpEvent], Awaitable[None]]
        self.on_tool: Callable[[OmpHostToolCall], Awaitable[OmpToolResult]]
        self.on_ui: Callable[[OmpUIRequest], Awaitable[OmpUIResponse]]

    async def _prompt_body(
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
    recorder: ActivityRecorder
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
    monkeypatch.setattr(
        "band.adapters.omp.lifecycle.OmpRpcClient", lambda *a, **kw: runtime
    )
    recorder = ActivityRecorder()
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
