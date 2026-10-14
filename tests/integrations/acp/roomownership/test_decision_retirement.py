"""A slow answer acknowledgment cannot resolve a successor's decision."""

from __future__ import annotations

import asyncio

from band.integrations.acp.client_profiles import CURSOR_ASK_QUESTION_METHOD
from band.integrations.acp.cursor import DECISION_NOT_PENDING_TEMPLATE
from tests.integrations.acp.acp_toolkit import DEFAULT_ROOM, accepted_work_finished
from tests.integrations.acp.acp_toolkit.harness import TranscriptTools
from tests.integrations.acp.roomownership.kit import (
    CANCELLED,
    MODE_QUESTION,
    QuestioningAgent,
    answer,
    answered,
    cursor,
    question_token,
)


class TestDecisionRetirement:
    async def test_a_held_answer_acknowledgment_cannot_settle_a_rejoined_room(
        self,
    ) -> None:
        peer = QuestioningAgent()
        async with cursor(peer) as session:
            old_question = await session.send("old question")
            old_room = session.room()
            old_session = session.session_id(DEFAULT_ROOM)
            old_client = session.runtime().client
            assert old_client is not None
            other_question = await session.send("other question", room="other-room")
            other_session = session.session_id("other-room")
            answer_tools = TranscriptTools()
            acknowledgment = answer_tools.hold_message("resolved")
            answering = asyncio.create_task(
                session.send(
                    answer(question_token(old_question), "agent"), tools=answer_tools
                )
            )

            async with acknowledgment:
                await accepted_work_finished(old_room)
                await session.adapter.on_cleanup(DEFAULT_ROOM)
                new_question = await session.send("new question")
                new_room = session.room()
                new_session = session.session_id(DEFAULT_ROOM)
                assert new_room is not old_room
                assert new_room.generation == old_room.generation
                stale = await old_client.ext_method(
                    CURSOR_ASK_QUESTION_METHOD,
                    {
                        "questions": [
                            {
                                "id": MODE_QUESTION.id,
                                "options": [{"id": MODE_QUESTION.options[0]}],
                            }
                        ]
                    },
                )
                late_answer = await session.send(
                    answer(question_token(old_question), "plan")
                )

            await answering
            assert stale == CANCELLED
            assert late_answer.texts == [
                DECISION_NOT_PENDING_TEMPLATE.format(token=question_token(old_question))
            ]
            assert new_session not in peer.answers
            assert other_session not in peer.answers
            await session.send(
                answer(question_token(other_question), "plan"), room="other-room"
            )
            await accepted_work_finished(session.room("other-room"))
            await session.send(answer(question_token(new_question), "plan"))
            await accepted_work_finished(new_room)

            assert peer.answers == {
                old_session: answered("agent"),
                other_session: answered("plan"),
                new_session: answered("plan"),
            }
