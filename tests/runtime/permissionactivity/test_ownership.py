from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import ExitStack

import pytest

from band.integrations.claude_sdk.dedup_tools import DedupingAgentTools
from band.runtime.activity import permission_wait
from band.runtime.execution import ExecutionContext
from band.runtime.toolactivity import report_tool_activity
from band.runtime.tools.agent import AgentTools
from band.testing.activity import ActivityRecorder
from tests.activitysupport import PERMISSION_BODY, REASONING_BODY, tool_body
from tests.conftest import make_message_event
from tests.runtime.permissionactivity.kit import (
    HeldTurn,
    RoomActivityPlatform,
    cancel_resolution,
    fail_resolution,
)
from tests.runtime.scheduler.driver import attempt_delivery


@pytest.mark.looptime
class TestPermissionOwnership:
    async def test_tools_without_a_turn_observer_preserve_resolution_errors(
        self,
        held_room: Callable[[str], tuple[ExecutionContext, HeldTurn]],
        permission_platform: RoomActivityPlatform,
    ) -> None:
        ctx, _ = held_room("room-unbound")
        await attempt_delivery(ctx, make_message_event(room_id=ctx.room_id))
        tools = AgentTools(ctx.room_id, ctx.link.rest)
        failure = ValueError("approval failed")
        with pytest.raises(ValueError) as raised, permission_wait(tools):
            assert await permission_platform.checkpoint(ctx) == REASONING_BODY
            raise failure
        assert raised.value is failure
        assert await permission_platform.checkpoint(ctx) == REASONING_BODY

    async def test_rooms_with_the_same_request_id_keep_independent_owners(
        self,
        held_room: Callable[[str], tuple[ExecutionContext, HeldTurn]],
        permission_platform: RoomActivityPlatform,
    ) -> None:
        left, left_turn = held_room("room-left")
        right, right_turn = held_room("room-right")
        await asyncio.gather(
            attempt_delivery(
                left, make_message_event(room_id=left.room_id, msg_id="same")
            ),
            attempt_delivery(
                right, make_message_event(room_id=right.room_id, msg_id="same")
            ),
        )
        with ExitStack() as left_waits, ExitStack() as right_waits:
            left_waits.enter_context(permission_wait(left_turn.tools[0]))
            right_waits.enter_context(permission_wait(right_turn.tools[0]))
            assert await permission_platform.checkpoint(left) == PERMISSION_BODY
            assert await permission_platform.checkpoint(right) == PERMISSION_BODY
            left_waits.close()
            assert await permission_platform.checkpoint(left) == REASONING_BODY
            assert await permission_platform.checkpoint(right) == PERMISSION_BODY
            await left_turn.finish(left)
            assert await permission_platform.checkpoint(right) == PERMISSION_BODY
        assert await permission_platform.checkpoint(right) == REASONING_BODY

    async def test_distinct_tools_and_a_wrapper_share_the_actual_turn(
        self,
        held_room: Callable[[str], tuple[ExecutionContext, HeldTurn]],
        permission_platform: RoomActivityPlatform,
    ) -> None:
        ctx, turn = held_room("room-shared")
        await attempt_delivery(ctx, make_message_event(room_id=ctx.room_id))
        first, second = turn.tools
        assert first is not second
        with permission_wait(DedupingAgentTools(second)):
            with permission_wait(first):
                await report_tool_activity(first, "Read")
                assert await permission_platform.checkpoint(ctx) == PERMISSION_BODY
            assert await permission_platform.checkpoint(ctx) == PERMISSION_BODY
        assert await permission_platform.checkpoint(ctx) == tool_body("Read")

    async def test_scope_captures_the_observer_before_tools_are_rebound(
        self,
        held_room: Callable[[str], tuple[ExecutionContext, HeldTurn]],
        permission_platform: RoomActivityPlatform,
    ) -> None:
        ctx, turn = held_room("room-capture")
        await attempt_delivery(ctx, make_message_event(room_id=ctx.room_id))
        lease = permission_wait(turn.tools[0])
        recorder = ActivityRecorder()
        recorder.observing(turn.tools[0])
        with lease:
            assert await permission_platform.checkpoint(ctx) == PERMISSION_BODY
        assert await permission_platform.checkpoint(ctx) == REASONING_BODY
        assert recorder.events == []

    @pytest.mark.parametrize(
        ("terminate", "error"),
        [(fail_resolution, ValueError), (cancel_resolution, asyncio.CancelledError)],
        ids=["error", "cancel"],
    )
    async def test_failed_or_cancelled_resolution_releases_its_owner(
        self,
        held_room: Callable[[str], tuple[ExecutionContext, HeldTurn]],
        permission_platform: RoomActivityPlatform,
        terminate: Callable[[asyncio.Task[None], asyncio.Future[None]], None],
        error: type[BaseException],
    ) -> None:
        ctx, turn = held_room("room-finalize")
        await attempt_delivery(ctx, make_message_event(room_id=ctx.room_id))
        entered = asyncio.Event()
        resolve: asyncio.Future[None] = asyncio.get_running_loop().create_future()

        async def resolve_permission() -> None:
            with permission_wait(turn.tools[0]):
                entered.set()
                await resolve

        resolver = asyncio.create_task(resolve_permission())
        try:
            await entered.wait()
            assert await permission_platform.checkpoint(ctx) == PERMISSION_BODY
            terminate(resolver, resolve)
            with pytest.raises(error):
                await resolver
            assert await permission_platform.checkpoint(ctx) == REASONING_BODY
        finally:
            resolver.cancel()
            await asyncio.gather(resolver, return_exceptions=True)

    async def test_retired_room_lease_cannot_clear_a_rejoined_room(
        self,
        held_room: Callable[[str], tuple[ExecutionContext, HeldTurn]],
        permission_platform: RoomActivityPlatform,
    ) -> None:
        old, old_turn = held_room("room-rejoined")
        await attempt_delivery(old, make_message_event(room_id=old.room_id))
        with ExitStack() as old_waits:
            old_waits.enter_context(permission_wait(old_turn.tools[0]))
            await old_turn.finish(old)
            await old.stop()
            current, current_turn = held_room(old.room_id)
            await attempt_delivery(current, make_message_event(room_id=current.room_id))
            with permission_wait(current_turn.tools[0]):
                old_waits.close()
                assert await permission_platform.checkpoint(current) == PERMISSION_BODY
            assert await permission_platform.checkpoint(current) == REASONING_BODY
