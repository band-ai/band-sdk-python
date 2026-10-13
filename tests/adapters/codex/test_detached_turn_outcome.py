"""Codex adapter: a turn released to wait on a human is judged at its real end."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from band.core.exceptions import InputNotAccepted
from band.testing import (
    MISSING_REPLY_FAILURE,
    events_of_type,
    failure_reports,
)
from tests.adapters.codex.kit import (
    ROOM_ID,
    CodexRoom,
    parked_on_approval,
)
from tests.adapters.codexturns import (
    final_text,
    turn_completed,
)


class TestDetachedTurnOutcome:
    """A turn released to wait on a human is judged at its real end."""

    @pytest.mark.asyncio
    async def test_a_released_turn_that_ends_silently_is_reported_once(
        self, codex_room: Callable[..., Awaitable[CodexRoom]]
    ) -> None:
        room = await codex_room(*parked_on_approval(turn_completed()))

        await room.send("run tests")
        await room.send("/approve req-10")
        await room.settled()

        turn, approval = room.deliveries
        assert failure_reports(turn) == [MISSING_REPLY_FAILURE]
        assert failure_reports(approval) == []

    @pytest.mark.asyncio
    async def test_messages_during_a_released_turn_never_take_over_its_reply(
        self, codex_room: Callable[..., Awaitable[CodexRoom]]
    ) -> None:
        room = await codex_room(
            *parked_on_approval(final_text("Tests pass."), turn_completed())
        )

        await room.send("run tests")
        with pytest.raises(InputNotAccepted):
            await room.send("and lint too")
        await room.send("/approve req-10")
        await room.settled()

        turn, busy, approval = room.deliveries
        assert [e["content"] for e in events_of_type(turn, "thought")] == [
            "Codex approval request handled automatically (accept).",
            "Tests pass.",
        ]
        assert failure_reports(turn) == [MISSING_REPLY_FAILURE]
        assert busy.chat == []
        assert not busy.turn.complete
        assert turn.turn.complete and approval.turn.complete
        assert failure_reports(busy) == failure_reports(approval) == []

    @pytest.mark.asyncio
    async def test_a_released_turn_ended_by_room_cleanup_posts_nothing(
        self, codex_room: Callable[..., Awaitable[CodexRoom]]
    ) -> None:
        room = await codex_room(*parked_on_approval(turn_completed()))

        await room.send("run tests")
        await room.adapter.on_cleanup(ROOM_ID)

        [turn] = room.deliveries
        assert failure_reports(turn) == []
