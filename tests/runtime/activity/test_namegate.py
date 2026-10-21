from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx

from band.core.activity import sync_activity_tool_names
from band.core.types import Emit
from band.platform.event import PlatformEvent
from band.runtime.execution import ExecutionContext
from band.runtime.tools.agent import AgentTools
from band.runtime.working_state import (
    end_tool_activity,
    report_memory_activity,
    report_tool_activity,
    set_permission_wait,
    tool_call_activity,
)
from tests.conftest import make_message_event
from tests.runtime.activity.kit import ActivityPeer, activity_context


class DetailPeer(ActivityPeer):
    """Capture the activity detail each report carries, alongside the base tuples."""

    def __init__(self) -> None:
        super().__init__()
        self.details: list[dict[str, object] | None] = []

    def answer(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/activity"):
            self.details.append(json.loads(request.content).get("activity"))
        return super().answer(request)


def _reporting_handler(
    *, tool: str | None = None, permission: bool = False, memory: bool = False
):
    async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
        tools = AgentTools.from_context(ctx)
        if permission:
            await set_permission_wait(tools, True)
            await set_permission_wait(tools, False)
        if memory:
            await report_memory_activity(tools)
        if tool is not None:
            await report_tool_activity(tools, tool)
            await end_tool_activity(tools)

    return handler


class TestActivityToolNameGate:
    async def test_tool_names_report_while_tool_calls_are_emitted(self) -> None:
        peer = DetailPeer()
        async with activity_context(
            peer,
            _reporting_handler(tool="Bash"),
            AsyncMock(),
        ) as ctx:
            await ctx._process_event(make_message_event(msg_id="trigger"))
        kinds = [detail and detail.get("kind") for detail in peer.details]
        assert kinds.count("tool_call") == 1
        named = [d for d in peer.details if d and d.get("kind") == "tool_call"]
        assert named[0]["name"] == "Bash"

    async def test_tool_names_are_dropped_when_tool_calls_are_not_emitted(
        self,
    ) -> None:
        peer = DetailPeer()
        async with activity_context(
            peer,
            _reporting_handler(tool="Bash"),
            AsyncMock(),
        ) as ctx:
            ctx.set_activity_tool_names(False)
            await ctx._process_event(make_message_event(msg_id="trigger"))
        kinds = [detail and detail.get("kind") for detail in peer.details]
        assert "tool_call" not in kinds
        assert "reasoning" in kinds
        for detail in peer.details:
            assert detail is None or not detail.get("name")

    async def test_permission_and_memory_survive_the_name_gate(self) -> None:
        peer = DetailPeer()
        async with activity_context(
            peer,
            _reporting_handler(permission=True, memory=True),
            AsyncMock(),
        ) as ctx:
            ctx.set_activity_tool_names(False)
            await ctx._process_event(make_message_event(msg_id="trigger"))
        kinds = [detail and detail.get("kind") for detail in peer.details]
        assert "memory" in kinds
        assert "permission" in kinds

    async def test_flip_after_binding_hides_names_in_the_running_turn(
        self,
    ) -> None:
        # The codex ordering: the observer is bound to the turn before the
        # adapter's emit sync flips the flag. The gate must read the live
        # choice at report time, not the choice at construction time.
        peer = DetailPeer()
        bound: list = []

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            bound.append(ctx.activity_observer(ctx.current_scope))
            ctx.set_activity_tool_names(False)
            await bound[0].update(tool_call_activity("Bash"))

        async with activity_context(peer, handler, AsyncMock()) as ctx:
            await ctx._process_event(make_message_event(msg_id="trigger"))
        tool_frames = [d for d in peer.details if d and d.get("kind") == "tool_call"]
        assert not tool_frames
        assert {"kind": "reasoning"} in peer.details

    async def test_sync_resolves_names_from_the_emit_choice(self) -> None:
        async with activity_context(
            ActivityPeer(), _reporting_handler(), AsyncMock()
        ) as ctx:
            tools = SimpleNamespace(_ctx=ctx)
            sync_activity_tool_names(tools, frozenset({Emit.TOOL_CALLS}))
            assert ctx._activity_tool_names is True
            sync_activity_tool_names(tools, frozenset({Emit.THOUGHTS}))
            assert ctx._activity_tool_names is False
            sync_activity_tool_names(SimpleNamespace(), frozenset())
            assert ctx._activity_tool_names is False
