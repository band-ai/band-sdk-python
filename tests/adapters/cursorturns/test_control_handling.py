"""A Cursor command reaches the parked provider through the control hook alone."""

from __future__ import annotations

import pytest

from band.core.exceptions import InputNotAccepted
from band.integrations.acp.cursor import (
    DECISION_RESOLVED_TEMPLATE,
    DECISION_UNAUTHORIZED_MESSAGE,
    ROOM_COMMAND,
    CursorCommandWord,
)
from tests.adapters.cursorturns.conftest import OpenCursor
from tests.adapters.cursorturns.kit import (
    ROOM_A,
    ROOM_B,
    active_turn,
    busy_provider,
    command,
    decision_token,
    finish_turn,
    occupy,
    terminated,
)

ALICE = "alice"
LIST_DECISIONS = f"{ROOM_COMMAND} {CursorCommandWord.DECISIONS}"


class TestCursorControlHandling:
    async def test_a_command_resolves_the_parked_provider_and_settles_its_delivery(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("approval")
        session = await open_cursor(provider.agent)
        await occupy(session, provider)
        turn = active_turn(session)
        token = decision_token(session)

        consumed, tools = await command(
            session, f"/cursor select {token} allow-1", sender_id=ALICE
        )
        await terminated(turn)

        assert consumed
        assert provider.agent.approved is True
        assert tools.reply.texts == [
            DECISION_RESOLVED_TEMPLATE.format(kind="permission", token=token)
        ]
        assert tools.turn.complete

    async def test_an_approval_behind_a_refused_ordinary_input_reaches_the_provider(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("approval")
        session = await open_cursor(provider.agent)
        await occupy(session, provider)
        turn = active_turn(session)
        token = decision_token(session)

        with pytest.raises(InputNotAccepted):
            await session.send("an ordinary follow-up", room=ROOM_A)
        await command(session, f"/cursor select {token} allow-1", sender_id=ALICE)
        await terminated(turn)
        await session.send("an ordinary follow-up", room=ROOM_A)

        assert provider.agent.approved is True
        assert len(provider.agent.prompts) == 2

    @pytest.mark.parametrize("room", [ROOM_A, ROOM_B])
    async def test_a_command_is_answered_while_the_provider_is_busy_without_a_prompt(
        self, open_cursor: OpenCursor, room: str
    ) -> None:
        provider = busy_provider("prompt")
        session = await open_cursor(provider.agent)
        sending = await occupy(session, provider)

        reply = await session.send(LIST_DECISIONS, room=room)
        await finish_turn(provider, sending)

        assert reply.texts == ["Pending Cursor decisions: none"]
        assert len(provider.agent.prompts) == 1

    async def test_an_unauthorized_command_is_refused_and_leaves_the_decision_pending(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("approval")
        session = await open_cursor(
            provider.agent, decision_authorized_senders=frozenset({ALICE})
        )
        await occupy(session, provider)
        turn = active_turn(session)
        token = decision_token(session)
        resolve = f"/cursor select {token} allow-1"

        _, refusal = await command(session, resolve, sender_id="mallory")
        _, resolution = await command(session, resolve, sender_id=ALICE)
        await terminated(turn)

        assert refusal.reply.texts == [DECISION_UNAUTHORIZED_MESSAGE]
        assert resolution.reply.texts == [
            DECISION_RESOLVED_TEMPLATE.format(kind="permission", token=token)
        ]
        assert provider.agent.approved is True
