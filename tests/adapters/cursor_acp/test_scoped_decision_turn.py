"""A Cursor turn parked on a room decision keeps its delivery's posting authority."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from acp.schema import AllowedOutcome, DeniedOutcome

from band.integrations.acp.cursor import (
    DECISION_RESOLVED_TEMPLATE,
    DECISION_TIMED_OUT_TEMPLATE,
    ROOM_COMMAND,
    CursorCommandWord,
)
from tests.adapters.cursor_acp.kit import (
    ALLOW_ONCE,
    ScopedCursorRoom,
    asks_then_replies,
)
from tests.integrations.acp.acp_toolkit.harness import decision_token

REPLY = "done"


class TestCursorScopedDecisionTurn:
    """Run by a real execution context, the room's decision reaches Cursor
    once, and the turn's later posts land after its delivery was released."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("command", "outcome"),
        [
            (f"{CursorCommandWord.SELECT} {{token}} {ALLOW_ONCE}", AllowedOutcome),
            (f"{CursorCommandWord.DENY} {{token}}", DeniedOutcome),
        ],
        ids=["select", "deny"],
    )
    async def test_a_room_decision_reaches_cursor_and_the_turn_still_replies(
        self,
        scoped_cursor_room: Callable[..., Awaitable[ScopedCursorRoom]],
        command: str,
        outcome: type[AllowedOutcome | DeniedOutcome],
    ) -> None:
        room = await scoped_cursor_room(asks_then_replies(REPLY))
        await room.deliver("ask")
        [prompt] = room.platform.room_messages
        token = decision_token(prompt)

        await room.deliver("decide", f"{ROOM_COMMAND} {command.format(token=token)}")
        await room.turn_ended()

        assert [type(r.outcome) for r in room.agent.permission_responses] == [outcome]
        assert room.platform.room_messages == [
            prompt,
            DECISION_RESOLVED_TEMPLATE.format(kind="permission", token=token),
            REPLY,
        ]
        assert room.platform.marked("processed") == ["ask", "decide"]

    @pytest.mark.asyncio
    @pytest.mark.looptime
    async def test_an_expired_decision_reaches_cursor_and_the_turn_still_replies(
        self, scoped_cursor_room: Callable[..., Awaitable[ScopedCursorRoom]]
    ) -> None:
        room = await scoped_cursor_room(asks_then_replies(REPLY), decision_timeout_s=5)
        await room.deliver("ask")
        [prompt] = room.platform.room_messages
        token = decision_token(prompt)

        await room.turn_ended()

        assert [type(r.outcome) for r in room.agent.permission_responses] == [
            DeniedOutcome
        ]
        assert room.platform.room_messages == [
            prompt,
            DECISION_TIMED_OUT_TEMPLATE.format(kind="permission", token=token),
            REPLY,
        ]
        assert room.platform.marked("processed") == ["ask"]
