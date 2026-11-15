"""Room work refused while a released Codex turn finishes runs once it ends."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from band.adapters.codex import CodexCommand
from band.client.streaming import ControlMode
from band.platform.event import ReconnectedEvent
from band.runtime.control import apply_control
from tests.adapters.codex.activity.kit import LIST_FILES, item_completed, item_started
from tests.adapters.codex.kit import answered_turn
from tests.adapters.codex.scheduledroom import (
    HELD_TURNS,
    RELEASED_TURN,
    ScheduledCodexRoom,
)
from tests.adapters.codexturns import FakeCodexClient, await_released_turn
from tests.runtime.conftest import Waiting, wait_for_condition

HELD = ["first", "second"]


class TestWorkAfterReleasedTurn:
    @pytest.mark.asyncio
    async def test_messages_sent_after_the_final_reply_run_in_order_once_the_turn_ends(
        self, scheduled_room: ScheduledCodexRoom
    ) -> None:
        await scheduled_room.open_window()
        await scheduled_room.hold(*HELD)

        scheduled_room.end_turn()

        await scheduled_room.processes("ask", "approve", *HELD)
        assert scheduled_room.platform.marked("failed") == []
        assert scheduled_room.turn_starts == 1 + len(HELD_TURNS)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "hold",
        [ScheduledCodexRoom.hold, ScheduledCodexRoom.hold_missed],
        ids=["delivered", "missed by the socket"],
    )
    async def test_a_control_in_the_window_is_answered_while_ordinary_work_waits(
        self,
        scheduled_room: ScheduledCodexRoom,
        hold: Callable[..., Awaitable[None]],
    ) -> None:
        await scheduled_room.open_window()
        await hold(scheduled_room, *HELD)

        await scheduled_room.deliver("status", f"/{CodexCommand.STATUS}")
        await scheduled_room.processes("ask", "approve", "status")
        scheduled_room.end_turn()

        await scheduled_room.processes("ask", "approve", "status", *HELD)

    @pytest.mark.asyncio
    async def test_a_command_that_ends_before_its_turn_retries_nothing(
        self, scheduled_room: ScheduledCodexRoom
    ) -> None:
        await scheduled_room.open_window()
        scheduled_room.client.emit_turn(RELEASED_TURN, item_started(LIST_FILES))
        await scheduled_room.client.read_by_adapter()
        (command,) = scheduled_room.adapter._room_clients[
            scheduled_room.ctx.room_id
        ].commands.values()
        await scheduled_room.hold(*HELD)
        reads = len(scheduled_room.platform.next_reads)
        attempts = scheduled_room.platform.requested("processing")

        scheduled_room.client.emit_turn(RELEASED_TURN, item_completed(LIST_FILES))
        assert command.owner is not None
        await command.owner.task
        await Waiting.suspended(scheduled_room.ctx)

        assert len(scheduled_room.platform.next_reads) == reads
        assert scheduled_room.platform.requested("processing") == attempts
        scheduled_room.end_turn()
        await scheduled_room.processes("ask", "approve", *HELD)

    @pytest.mark.asyncio
    async def test_held_work_waits_through_a_stop_and_runs_in_order_after_play(
        self, scheduled_room: ScheduledCodexRoom
    ) -> None:
        await scheduled_room.open_window()
        await scheduled_room.hold(*HELD)

        # Band serves a stopped room no /next work until PLAY.
        scheduled_room.platform.stopped = True
        await apply_control(
            scheduled_room.ctx, ControlMode.STOP, scheduled_room.adapter.on_interrupt
        )
        await await_released_turn(scheduled_room.adapter, scheduled_room.ctx.room_id)
        await Waiting.suspended(scheduled_room.ctx)
        assert scheduled_room.processed == ["ask", "approve"]

        scheduled_room.client.script(
            *(event for turn in HELD_TURNS for event in answered_turn(turn))
        )
        scheduled_room.platform.stopped = False
        await apply_control(
            scheduled_room.ctx, ControlMode.PLAY, scheduled_room.adapter.on_interrupt
        )

        await scheduled_room.processes("ask", "approve", *HELD)
        assert scheduled_room.platform.marked("failed") == []

    @pytest.mark.asyncio
    async def test_a_reconnect_in_the_window_runs_each_held_message_once(
        self, scheduled_room: ScheduledCodexRoom
    ) -> None:
        await scheduled_room.open_window()
        await scheduled_room.hold(*HELD)

        reads = len(scheduled_room.platform.next_reads)
        await scheduled_room.ctx.on_event(ReconnectedEvent())
        await wait_for_condition(
            lambda: len(scheduled_room.platform.next_reads) > reads
        )
        await Waiting.suspended(scheduled_room.ctx)
        assert scheduled_room.processed == ["ask", "approve"]
        scheduled_room.end_turn()

        await scheduled_room.processes("ask", "approve", *HELD)
        assert scheduled_room.turn_starts == 1 + len(HELD_TURNS)

    @pytest.mark.asyncio
    async def test_a_turn_that_never_released_the_room_requests_no_extra_pass(
        self,
        codex_scheduled: Callable[[FakeCodexClient], Awaitable[ScheduledCodexRoom]],
    ) -> None:
        room = await codex_scheduled(
            FakeCodexClient(events=list(answered_turn(RELEASED_TURN)))
        )
        await Waiting.suspended(room.ctx)
        reads = len(room.platform.next_reads)

        await room.deliver("ask", "say hi")
        await room.processes("ask")
        await Waiting.suspended(room.ctx)

        assert len(room.platform.next_reads) == reads
