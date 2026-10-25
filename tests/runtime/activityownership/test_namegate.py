"""Tool names reach the room's activity detail only while tool-call narration
is on; permission and memory activity name no content and report either way."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from functools import partial

import pytest

from band.core.types import AgentInput, Emit, HistoryProvider
from band.platform.event import PlatformEvent
from band.runtime.activity import permission_wait, tool_call_activity
from band.runtime.execution import ExecutionContext
from band.runtime.toolactivity import (
    report_memory_activity,
    report_tool_activity,
)
from band.runtime.tools.agent import AgentTools
from tests.activitysupport import (
    MEMORY_BODY,
    PERMISSION_BODY,
)
from tests.conftest import make_message_event
from tests.runtime.activityownership.kit import (
    ACTIVITY_TOOL_NAME,
    ActivityPlatform,
    ActivityToolAdapter,
    deliver_event,
)


def tool_names(platform: ActivityPlatform) -> list[str]:
    return [
        body["activity"]["name"]
        for body in platform.activity_bodies
        if body.get("activity", {}).get("kind") == "tool_call"
    ]


@pytest.mark.looptime
class TestActivityToolNameGate:
    @pytest.mark.parametrize(
        ("emit", "expected_names"),
        (((), []), ((Emit.TOOL_CALLS,), [ACTIVITY_TOOL_NAME])),
    )
    async def test_direct_turn_entry_gates_activity_names(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
        emit: tuple[Emit, ...],
        expected_names: list[str],
    ) -> None:
        adapter = ActivityToolAdapter(activity_platform, emit=emit)

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            tools = AgentTools.from_context(ctx)
            await adapter.run_judged_turn(
                msg=make_message_event().payload,
                tools=tools,
                history=HistoryProvider(raw=[]),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id=ctx.room_id,
            )

        ctx = execution(handler)
        await deliver_event(ctx, make_message_event())
        assert tool_names(activity_platform) == expected_names
        assert MEMORY_BODY in activity_platform.activity_bodies

    async def test_batch_turn_entry_gates_activity_names(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
    ) -> None:
        adapter = ActivityToolAdapter(activity_platform, emit=())

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            tools = AgentTools.from_context(ctx)
            first = make_message_event(msg_id="first").payload
            second = make_message_event(msg_id="second").payload
            await adapter.on_messages(
                AgentInput(
                    msg=first,
                    messages=(first, second),
                    tools=tools,
                    history=HistoryProvider(raw=[]),
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=False,
                    room_id=ctx.room_id,
                )
            )

        ctx = execution(handler)
        await deliver_event(ctx, make_message_event())
        assert tool_names(activity_platform) == []
        assert MEMORY_BODY in activity_platform.activity_bodies

    async def test_permission_and_memory_survive_the_name_gate(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
    ) -> None:
        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            tools = AgentTools.from_context(ctx)
            ctx.set_activity_tool_names(False)
            with ExitStack() as waits:
                await activity_platform.shown(
                    partial(waits.enter_context, permission_wait(tools))
                )
                await report_memory_activity(tools)
                await activity_platform.shown(waits.close)

        ctx = execution(handler)
        await deliver_event(ctx, make_message_event())
        assert PERMISSION_BODY in activity_platform.activity_bodies
        assert MEMORY_BODY in activity_platform.activity_bodies
        assert tool_names(activity_platform) == []

    async def test_flip_after_binding_hides_names_in_the_running_turn(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
    ) -> None:
        """The observer is bound to the turn before the adapter's emit sync
        flips the flag; the gate reads the live choice at report time."""

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            tools = AgentTools.from_context(ctx)
            observer = ctx.activity_observer(ctx.current_scope)
            assert observer is not None
            ctx.set_activity_tool_names(False)
            await observer.update(tool_call_activity(ACTIVITY_TOOL_NAME))
            await activity_platform.shown(report_memory_activity(tools))

        ctx = execution(handler)
        await deliver_event(ctx, make_message_event())
        assert tool_names(activity_platform) == []

    async def test_continuation_frame_after_takeover_never_reaches_wire(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
    ) -> None:
        """An observer that outlives its turn (a background continuation) is
        fenced once the next room-message turn owns activity: dropping the
        ownership check in ``observe_activity`` surfaces a third named frame."""
        bound: list = []
        turns = {"n": 0}

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            turns["n"] += 1
            tools = AgentTools.from_context(ctx)
            if turns["n"] == 1:
                bound.append(ctx.activity_observer(ctx.current_scope))
                await activity_platform.shown(report_tool_activity(tools, "bash"))
                return
            # The new turn owns activity; the continuation then wakes up.
            await activity_platform.shown(report_tool_activity(tools, "exec"))
            await bound[0].update(tool_call_activity("band_send_message"))
            await activity_platform.shown(report_memory_activity(tools))

        ctx = execution(handler)
        await deliver_event(ctx, make_message_event(msg_id="turn-one"))
        await deliver_event(ctx, make_message_event(msg_id="turn-two"))
        assert tool_names(activity_platform) == ["bash", "exec"]
