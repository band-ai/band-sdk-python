"""Only a completed seeding prompt makes a fresh ACP session the room's.

A seeding prompt the agent refuses or that raises leaves no resume record and
no mapping, so the next turn, recovery or restart starts a fresh session with
the full context and transcript. A restored session already holds the room's
context and is kept through a refusal.
"""

from __future__ import annotations

import pytest
from acp import RequestError

from band.converters.acp_client import ACPClientHistoryConverter
from band.integrations.acp.client_adapter import SYSTEM_CONTEXT_HEADER
from band.integrations.acp.types import REFUSAL_STOP
from tests.integrations.acp.acp_toolkit import (
    DEFAULT_ROOM,
    FakeACPAgent,
    TranscriptTools,
    acp_adapter,
)
from tests.integrations.acp.acp_toolkit.agent import END_TURN_STOP
from tests.integrations.acp.clientbehavior.kit import (
    MARCO_REPLAY_LINE,
    QUOTA_TEXT,
    marco_message,
    rehydration_history,
    resume_records,
    seeded,
)
from tests.integrations.acp.roomownership.kit import fails_prompt, recover

QUESTION = "What is my favorite color?"


class TestSessionSeeding:
    @pytest.mark.asyncio
    async def test_a_refused_seed_leaves_the_next_turn_a_new_seeded_session(
        self,
    ) -> None:
        agent = FakeACPAgent().will_say(QUOTA_TEXT).stops_with(REFUSAL_STOP)
        refusing = TranscriptTools()
        async with acp_adapter(agent) as session:
            await session.send(
                "first?",
                bootstrap=True,
                history=rehydration_history(MARCO_REPLAY_LINE),
                tools=refusing,
            )
            runtime = session.runtime()
            agent.stops_with(END_TURN_STOP)
            await session.send(QUESTION, room_context=[marco_message()])

            assert session.runtime() is runtime
            assert agent.lifecycle == []
            await session.retired_sessions_closed()
        refused, fresh = agent.session_ids()
        assert resume_records(refusing) == {}
        assert [prompt["session_id"] for prompt in agent.prompts] == [refused, fresh]
        assert agent.closed_sessions == [refused]
        assert seeded(agent.prompt_texts()[1])

    @pytest.mark.asyncio
    async def test_a_raising_seed_leaves_the_next_turn_a_new_seeded_session(
        self,
    ) -> None:
        agent = FakeACPAgent(supports_session_load=True)
        fails_prompt(agent, 1, RequestError.internal_error())
        failing = TranscriptTools()
        async with acp_adapter(agent) as session:
            with pytest.raises(RequestError):
                await session.send(
                    "first?",
                    bootstrap=True,
                    history=rehydration_history(MARCO_REPLAY_LINE),
                    tools=failing,
                )
            (failed,) = agent.session_ids()
            agent.knows_session(failed)
            await session.send(QUESTION, room_context=[marco_message()])

        _, fresh = agent.session_ids()
        assert resume_records(failing) == {}
        assert agent.session_load_requests == []
        assert agent.prompts[1]["session_id"] == fresh
        assert seeded(agent.prompt_texts()[1])

    @pytest.mark.asyncio
    async def test_a_refused_seed_leaves_nothing_to_resume_after_a_restart(
        self,
    ) -> None:
        agent = FakeACPAgent(supports_session_load=True).stops_with(REFUSAL_STOP)
        converter = ACPClientHistoryConverter()
        tools = TranscriptTools()
        async with acp_adapter(agent) as session:
            await session.send(
                "first?",
                bootstrap=True,
                history=converter.convert([marco_message()]),
                tools=tools,
            )
        (refused,) = agent.session_ids()
        agent.knows_session(refused)
        history = converter.convert([marco_message(), *tools.events_sent])

        agent.stops_with(END_TURN_STOP)
        async with acp_adapter(agent) as session:
            await session.send(QUESTION, bootstrap=True, history=history)

        assert history.room_to_session == {}
        _, fresh = agent.session_ids()
        assert agent.session_load_requests == []
        assert agent.prompts[1]["session_id"] == fresh
        assert seeded(agent.prompt_texts()[1])

    @pytest.mark.asyncio
    async def test_a_restart_after_a_refused_replacement_resumes_the_seeded_session(
        self,
    ) -> None:
        """The defined outcome: the newest seeded session is the room's, even
        when a later replacement for it was refused."""
        agent = FakeACPAgent(supports_session_load=True)
        converter = ACPClientHistoryConverter()
        seeding, refusing = TranscriptTools(), TranscriptTools()
        async with acp_adapter(agent) as session:
            await session.send(
                "first?",
                bootstrap=True,
                history=converter.convert([marco_message()]),
                tools=seeding,
            )
        (kept,) = agent.session_ids()
        # The agent has lost ``kept``, so the restart replaces it.
        replacing = converter.convert([marco_message(), *seeding.events_sent])
        agent.stops_with(REFUSAL_STOP)
        async with acp_adapter(agent) as session:
            await session.send(
                "second?", bootstrap=True, history=replacing, tools=refusing
            )

        agent.knows_session(kept).stops_with(END_TURN_STOP)
        history = converter.convert(
            [marco_message(), *seeding.events_sent, *refusing.events_sent]
        )
        async with acp_adapter(agent) as session:
            await session.send(QUESTION, bootstrap=True, history=history)

        assert history.room_to_session == {DEFAULT_ROOM: kept}
        assert agent.session_load_requests == [kept, kept]
        assert agent.prompts[2]["session_id"] == kept
        prompt = agent.prompt_texts()[2]
        assert SYSTEM_CONTEXT_HEADER in prompt
        assert MARCO_REPLAY_LINE not in prompt

    @pytest.mark.asyncio
    async def test_a_refused_seed_leaves_nothing_to_reload_after_recovery(
        self,
    ) -> None:
        agent = FakeACPAgent(supports_session_load=True).stops_with(REFUSAL_STOP)
        async with acp_adapter(agent) as session:
            await session.send(
                "first?",
                bootstrap=True,
                history=rehydration_history(MARCO_REPLAY_LINE),
            )
            await recover(session)
            agent.stops_with(END_TURN_STOP)
            await session.send(QUESTION, room_context=[marco_message()])

        _, fresh = agent.session_ids()
        assert agent.session_load_requests == []
        assert agent.prompts[1]["session_id"] == fresh
        assert seeded(agent.prompt_texts()[1])

    @pytest.mark.asyncio
    async def test_a_refused_later_prompt_keeps_the_seeded_session(
        self, fake_agent: FakeACPAgent
    ) -> None:
        """Only the refused prompt leaves the session's context; what seeded
        it stays, so the session and its resume record are kept."""
        fake_agent.will_say(QUOTA_TEXT)
        refusing = TranscriptTools()
        async with acp_adapter(fake_agent) as session:
            await session.send("first")
            fake_agent.stops_with(REFUSAL_STOP)
            reply = await session.send_judged("second", tools=refusing)
            kept = session.session_id(DEFAULT_ROOM)
            fake_agent.stops_with(END_TURN_STOP)
            await session.send("third")

        assert fake_agent.session_ids() == [kept]
        assert [prompt["session_id"] for prompt in fake_agent.prompts] == [kept] * 3
        assert fake_agent.closed_sessions == []
        assert reply.errors == [QUOTA_TEXT]
        assert resume_records(refusing) == {DEFAULT_ROOM: kept}
        assert SYSTEM_CONTEXT_HEADER not in fake_agent.prompt_texts()[2]

    @pytest.mark.asyncio
    async def test_a_refused_first_prompt_keeps_a_loaded_session(self) -> None:
        """A loaded session kept the room's context, so it stays; its next
        prompt still owes the Band context but never the transcript."""
        agent = FakeACPAgent(supports_session_load=True).knows_session("loaded")
        agent.stops_with(REFUSAL_STOP)
        refusing = TranscriptTools()
        async with acp_adapter(agent) as session:
            await session.send(
                "first?",
                bootstrap=True,
                history=rehydration_history(MARCO_REPLAY_LINE, session="loaded"),
                tools=refusing,
            )
            agent.stops_with(END_TURN_STOP)
            await session.send(QUESTION, room_context=[marco_message()])

        assert agent.session_ids() == []
        assert [prompt["session_id"] for prompt in agent.prompts] == ["loaded"] * 2
        assert agent.closed_sessions == []
        assert resume_records(refusing) == {}
        prompt = agent.prompt_texts()[1]
        assert SYSTEM_CONTEXT_HEADER in prompt
        assert MARCO_REPLAY_LINE not in prompt
