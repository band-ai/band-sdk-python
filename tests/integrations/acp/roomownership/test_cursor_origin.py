"""Cursor's sessionless requests resolve against the process that sent them."""

from __future__ import annotations

import asyncio

import pytest

from band.integrations.acp.client_profiles import CURSOR_ASK_QUESTION_METHOD
from tests.integrations.acp.acp_toolkit import (
    DEFAULT_ROOM,
    FakeACPAgent,
    accepted_work_finished,
    pending_decisions,
)
from tests.integrations.acp.acp_toolkit.agent import Gate
from tests.integrations.acp.roomownership.kit import (
    CANCELLED,
    TEST_GUARD_S,
    QuestioningAgent,
    answered,
    cursor,
    question_token,
    recover,
)


class TestCursorOrigin:
    """Cursor's sessionless requests resolve against the process that sent them."""

    @pytest.mark.asyncio
    async def test_two_rooms_park_and_resolve_their_own_questions(self) -> None:
        agent = QuestioningAgent()
        async with cursor(agent) as session:
            room_a = await session.send("ask", room="room-a")
            room_b = await session.send("ask", room="room-b")

            await session.send(
                f"/cursor answer {question_token(room_b)} mode=plan", room="room-b"
            )
            await session.send(
                f"/cursor answer {question_token(room_a)} mode=agent", room="room-a"
            )
            for room in ("room-a", "room-b"):
                await accepted_work_finished(session.room(room))

            assert agent.answers == {
                session.session_id("room-a"): answered("agent"),
                session.session_id("room-b"): answered("plan"),
            }

    @pytest.mark.asyncio
    async def test_a_parked_question_pins_its_process_and_stays_answerable(
        self,
    ) -> None:
        agent = QuestioningAgent()
        async with cursor(agent) as session:
            asked = await session.send("ask")
            room = session.room()

            assert room.retained.snapshot.leases == 1
            assert room.retained.snapshot.accepted == 1
            assert not session.runtime().closed

            await session.send(f"/cursor answer {question_token(asked)} mode=plan")
            await accepted_work_finished(room)

            assert room.retained.snapshot.leases == 0
            assert agent.answers == {session.session_id(DEFAULT_ROOM): answered("plan")}

    @pytest.mark.asyncio
    async def test_an_old_process_cannot_open_a_decision_on_its_successor(
        self,
    ) -> None:
        agent = FakeACPAgent()
        parked = Gate()
        prompts = 0

        @agent.on_prompt
        async def park_second(peer: FakeACPAgent, session_id: str) -> None:
            nonlocal prompts
            prompts += 1
            if prompts == 2:
                parked.received.set()
                await parked.release.wait()

        async with cursor(agent) as session:
            await session.send("hello")
            old_client = session.runtime().client
            assert old_client is not None
            await recover(session)
            successor = asyncio.create_task(session.send("again"))
            await parked.received.wait()

            async with asyncio.timeout(TEST_GUARD_S):
                stale = await old_client.ext_method(
                    CURSOR_ASK_QUESTION_METHOD,
                    {"questions": [{"id": "mode", "options": [{"id": "agent"}]}]},
                )

            assert stale == CANCELLED
            assert pending_decisions(session.adapter) == {}
            parked.release.set()
            await successor
            assert session.last_reply.texts == []

    @pytest.mark.asyncio
    async def test_a_sessionless_update_lands_in_its_rooms_open_turn(self) -> None:
        agent = FakeACPAgent().will_update_cursor_todos("ship it", sessionless=True)
        async with cursor(agent) as session:
            reply = await session.send("hello")

        assert reply.plans == ["- [ ] ship it"]
