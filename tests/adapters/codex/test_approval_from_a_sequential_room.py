"""Codex adapter: band hands a room its messages one at a time, so the reply resolving an approval is delivered only after the asking message's on_message returns. Approval waits are an hour long: a regression that waits on the human hangs until pytest's timeout instead of passing by luck."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from band.adapters.codex import CodexCommand
from band.client.streaming import ControlMode
from band.core.exceptions import InputNotAccepted
from band.integrations.codex.types import CodexApprovalMethod, CodexRequestMethod
from tests.adapters.codex.kit import (
    ACCEPTED,
    DECLINED,
    PATIENT_APPROVAL_S,
    ROOM_ID,
    CodexRoom,
    ToolSchemaFakeTools,
    make_platform_message,
    parked_on_approval,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    event_request,
    turn_completed,
)


class TestApprovalFromASequentialRoom:
    """Band hands a room its messages one at a time, so the reply resolving an
    approval is delivered only after the asking message's on_message returns.
    Approval waits are an hour long: a regression that waits on the human
    hangs until pytest's timeout instead of passing by luck."""

    @pytest.mark.asyncio
    async def test_the_reply_delivered_after_the_asking_message_resolves_it(
        self, codex_room: Callable[..., Awaitable[CodexRoom]]
    ) -> None:
        room = await codex_room(
            *parked_on_approval(turn_completed()),
            approval_wait_timeout_s=PATIENT_APPROVAL_S,
        )

        await room.send("run tests")
        control_tools = ToolSchemaFakeTools()
        assert await room.adapter.try_handle_control(
            make_platform_message(
                room_id=ROOM_ID, content=f"/{CodexCommand.APPROVE} req-10"
            ),
            control_tools,
        )
        await room.settled()

        assert control_tools.turn.complete
        assert room.client.responses == [(10, ACCEPTED)]

    @pytest.mark.asyncio
    async def test_a_request_while_a_turn_awaits_a_human_is_turned_away(
        self, codex_room: Callable[..., Awaitable[CodexRoom]]
    ) -> None:
        room = await codex_room(
            *parked_on_approval(turn_completed()),
            approval_wait_timeout_s=PATIENT_APPROVAL_S,
        )

        await room.send("run tests")
        with pytest.raises(InputNotAccepted):
            await room.send("and lint too")

        assert not room.deliveries[-1].turn.complete
        methods = room.client.request_methods
        assert methods.count(CodexRequestMethod.TURN_START) == 1

    @pytest.mark.asyncio
    async def test_interrupt_reaches_a_turn_parked_on_a_human(
        self, codex_room: Callable[..., Awaitable[CodexRoom]]
    ) -> None:
        """ExecutionContext.interrupt()/stop_room() only cancel the cycle task
        that invoked on_message -- once that returns early because the turn
        released the room, only on_interrupt still reaches the parked turn.

        on_interrupt declines the pending approval and then cancels the turn
        as a backstop; which of the two finishes it is an asyncio-scheduling
        detail, so this asserts only that the turn stops running."""
        room = await codex_room(
            *parked_on_approval(turn_completed()),
            approval_wait_timeout_s=PATIENT_APPROVAL_S,
        )

        await room.send("run tests")
        turn = room.turn
        assert not turn.done()

        await room.adapter.on_interrupt(ROOM_ID, ControlMode.INTERRUPT)

        assert turn.done()
        assert ROOM_ID not in room.adapter._pending_approvals

    @pytest.mark.asyncio
    async def test_cleanup_declines_a_parked_turn_instead_of_waiting(
        self, codex_room: Callable[..., Awaitable[CodexRoom]]
    ) -> None:
        """A turn waiting on a human holds the room's RPC lock; cleanup must
        decline its approval (and any it raises afterwards) rather than block
        for the whole approval wait."""
        room = await codex_room(
            *parked_on_approval(
                event_request(
                    11, "item/commandExecution/requestApproval", {"command": "b"}
                ),
                turn_completed(),
            ),
            approval_wait_timeout_s=PATIENT_APPROVAL_S,
        )

        await room.send("run tests")
        turn = room.turn
        await room.adapter.on_cleanup(ROOM_ID)
        await turn

        assert room.client.responses == [
            (10, DECLINED),
            (11, DECLINED),
        ]
        assert sum(m.startswith("Approval requested") for m in room.chat) == 1

    @pytest.mark.asyncio
    @pytest.mark.looptime
    async def test_cleanup_is_bounded_by_a_released_turn_still_working(
        self, codex_room: Callable[..., Awaitable[CodexRoom]]
    ) -> None:
        """Once released, the runtime can't cancel the turn; cleanup waits out
        the settle timeout, then cancels Codex work that never ends."""
        room = await codex_room(
            client=FakeCodexClient(events=list(parked_on_approval())),
            approval_wait_timeout_s=PATIENT_APPROVAL_S,
        )

        await room.send("run tests")
        await room.send(f"/{CodexCommand.APPROVE} req-10")
        turn = room.turn
        await room.adapter.on_cleanup(ROOM_ID)

        assert turn.cancelled()
        assert room.client.closed

    @pytest.mark.asyncio
    async def test_an_approval_codex_names_is_resolved_by_that_id(
        self, codex_room: Callable[..., Awaitable[CodexRoom]]
    ) -> None:
        room = await codex_room(
            event_request(
                7,
                CodexApprovalMethod.COMMAND_EXECUTION,
                {"approvalId": "ap-1", "command": ["git", "push"]},
            ),
            turn_completed(),
            approval_wait_timeout_s=PATIENT_APPROVAL_S,
        )

        await room.send("run protected command")
        assert any("Approval id: `ap-1`" in message for message in room.chat)
        await room.send(f"/{CodexCommand.APPROVE} ap-1")
        await room.settled()

        assert room.client.responses == [(7, ACCEPTED)]
        assert "Approval `ap-1` resolved as `accept`." in room.chat
