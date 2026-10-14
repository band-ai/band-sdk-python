"""Codex adapter: no reply."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from band.core.protocols import TurnResultAlreadyReported
from band.runtime.tools import BandTool
from band.testing import (
    events_of_type,
    reported_failures,
)
from tests.adapters.codex.kit import (
    CodexRoom,
    run_codex_turn,
)
from tests.adapters.codexturns import (
    final_text,
    tool_call_request,
    turn_completed,
)


class TestNoReply:
    @pytest.mark.asyncio
    async def test_no_reply_suppresses_the_closing_thought(self) -> None:
        turn = await run_codex_turn(
            events=[
                tool_call_request(1, BandTool.NO_REPLY, {"reason": "not for me"}),
                final_text("Nothing to add."),
                turn_completed(),
            ]
        )
        assert turn.tools.messages_sent == []
        assert reported_failures(turn.tools) == []

    @pytest.mark.asyncio
    async def test_no_reply_does_not_carry_into_the_next_turn(
        self, codex_room: Callable[..., Awaitable[CodexRoom]]
    ) -> None:
        room = await codex_room(
            tool_call_request(1, BandTool.NO_REPLY),
            turn_completed("turn-1"),
            final_text("Second answer"),
            turn_completed("turn-2"),
        )

        await room.send("First message")
        with pytest.raises(TurnResultAlreadyReported):
            await room.send("Second message")

        assert room.chat == []
        assert [
            e["content"] for e in events_of_type(room.deliveries[-1], "thought")
        ] == ["Second answer"]
