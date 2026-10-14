"""Judging a turn that detached on a pending decision."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from band.client.streaming import ControlMode
from band.testing import MISSING_REPLY_FAILURE, failure_reports
from tests.adapters.cursor_acp.kit import (
    CursorRoom,
    said,
)
from tests.integrations.acp.acp_toolkit.agent import (
    FakeACPAgent,
    allow_or_reject,
)
from tests.integrations.acp.acp_toolkit.harness import decision_token


class TestCursorACPAdapterDetachedTurn:
    """A turn parked on a decision releases its message early, so it is
    judged at its real end instead of when ``on_event`` returns."""

    @pytest.mark.asyncio
    async def test_a_detached_turn_that_ends_with_nothing_is_reported_after_release(
        self, cursor_room: Callable[..., Awaitable[CursorRoom]]
    ) -> None:
        room = await cursor_room(
            FakeACPAgent().will_ask_permission(
                title="shell", options=allow_or_reject("allow-once")
            )
        )

        # on_event returning normally is what keeps the delivery PROCESSED.
        turn = await room.send("run it")
        [prompt] = said(turn)
        assert failure_reports(turn) == []

        await room.send(f"/cursor deny {decision_token(prompt)}")
        await room.turns_finished()

        assert failure_reports(turn) == [MISSING_REPLY_FAILURE]

    @pytest.mark.asyncio
    async def test_a_decision_reply_settles_on_its_own_tools(
        self, cursor_room: Callable[..., Awaitable[CursorRoom]]
    ) -> None:
        """A decision reply is a whole turn of its own: its notice settles
        that message's tools, and neither it nor the decision prompt stands
        in for the parked turn's answer, which still relays there."""
        room = await cursor_room(
            FakeACPAgent()
            .will_ask_permission(title="shell", options=allow_or_reject("allow-once"))
            .will_call_mcp_tool(
                "reply",
                "band_send_message",
                arguments={"content": "ran it", "mentions": ["@alice"]},
            )
        )
        turn = await room.send("run it")
        [prompt] = said(turn)

        reply = await room.send(f"/cursor select {decision_token(prompt)} allow-once")
        await room.turns_finished()

        assert reply.turn.complete
        assert not reply.turn.replied
        assert failure_reports(reply) == []
        assert said(turn) == [prompt, "ran it"]
        assert failure_reports(turn) == []

    @pytest.mark.asyncio
    async def test_a_turn_woken_by_cleanup_is_judged_at_its_end(
        self, cursor_room: Callable[..., Awaitable[CursorRoom]]
    ) -> None:
        """Cleanup cancels the parked permission and the agent ends its turn
        within the settle grace, without answering the room."""
        room = await cursor_room(
            FakeACPAgent().will_ask_permission(
                title="shell", options=allow_or_reject("allow-once")
            )
        )
        turn = await room.send("run it")

        await room.adapter.on_cleanup("room-1")

        assert failure_reports(turn) == [MISSING_REPLY_FAILURE]

    @pytest.mark.asyncio
    async def test_an_interrupted_detached_turn_is_not_reported(
        self, cursor_room: Callable[..., Awaitable[CursorRoom]]
    ) -> None:
        """A room /stop ends the parked turn; the adapter settled it."""
        room = await cursor_room(
            FakeACPAgent().will_ask_permission(
                title="shell", options=allow_or_reject("allow-once")
            )
        )
        turn = await room.send("run it")

        await room.adapter.on_interrupt("room-1", ControlMode.STOP)
        await room.turns_finished()

        assert failure_reports(turn) == []

    @pytest.mark.asyncio
    async def test_a_detached_turn_cancelled_by_cleanup_is_reported_once(
        self, cursor_room: Callable[..., Awaitable[CursorRoom]]
    ) -> None:
        """With no settle grace, cleanup cancels the parked turn at once; its
        message was already handed back, so the turn reports it."""
        room = await cursor_room(
            FakeACPAgent().will_ask_permission(
                title="shell", options=allow_or_reject("allow-once")
            ),
            turn_settle_timeout_s=0,
        )
        turn = await room.send("run it")
        [prompt] = said(turn)

        await room.adapter.on_cleanup("room-1")

        assert said(turn) == [prompt]
        assert failure_reports(turn) == [MISSING_REPLY_FAILURE]
