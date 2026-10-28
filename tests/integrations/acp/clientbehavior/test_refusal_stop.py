"""A prompt the agent refuses fails its turn unless the turn already replied."""

from __future__ import annotations

import pytest
from acp.schema import StopReason
from band_sdk_core import AgentFailure, missing_reply_message

from band.converters.acp_client import ACPClientHistoryConverter
from band.core.protocols import TurnResultAlreadyReported
from band.core.types import Emit
from band.integrations.acp.client_adapter import SYSTEM_CONTEXT_HEADER
from band.integrations.acp.types import (
    ACP_FAILURE_PROVIDER,
    REFUSAL_FAILURE_MESSAGE,
    REFUSAL_STOP,
)
from band.runtime.tools import BandTool
from band.testing import reported_failures
from tests.integrations.acp.acp_toolkit import (
    FakeACPAgent,
    TranscriptTools,
    acp_adapter,
)
from tests.integrations.acp.acp_toolkit.agent import END_TURN_STOP
from tests.integrations.acp.clientbehavior.kit import (
    MARCO_REPLAY_LINE,
    codex_mcp_call,
    marco_message,
    rehydration_history,
)
from tests.integrations.acp.roomownership.kit import recover

QUOTA_TEXT = "You have exhausted your daily quota for this model."
QUESTION = "What is my favorite color?"


def refusal(message: str) -> dict[str, object]:
    """A refused turn's reported failure, as ``reported_failures`` lists it."""
    return AgentFailure(ACP_FAILURE_PROVIDER, message, REFUSAL_STOP).to_dict()


def seeded(prompt: str) -> bool:
    """Whether ``prompt`` carries the Band context and Marco's transcript."""
    return SYSTEM_CONTEXT_HEADER in prompt and MARCO_REPLAY_LINE in prompt


class TestRefusalStop:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "emit",
        [Emit.TOOL_CALLS | Emit.THOUGHTS | Emit.TASK_EVENTS, Emit.TOOL_CALLS],
        ids=["every-kind", "no-thoughts"],
    )
    async def test_a_refusal_fails_the_turn_once_with_the_agents_text(
        self, fake_agent: FakeACPAgent, emit: Emit
    ) -> None:
        fake_agent.will_say(QUOTA_TEXT).stops_with(REFUSAL_STOP)
        tools = TranscriptTools()
        async with acp_adapter(fake_agent, emit=emit) as session:
            reply = await session.send_judged("question?", tools=tools)

        assert reported_failures(tools) == [refusal(QUOTA_TEXT)]
        assert reply.outline == ["error"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "said", [(), (" \n",), ("\u200b",)], ids=["nothing", "blank", "zero-width"]
    )
    async def test_a_refusal_without_visible_text_says_the_agent_refused(
        self, fake_agent: FakeACPAgent, said: tuple[str, ...]
    ) -> None:
        for text in said:
            fake_agent.will_say(text)
        fake_agent.stops_with(REFUSAL_STOP)
        tools = TranscriptTools()
        async with acp_adapter(fake_agent) as session:
            reply = await session.send_judged("question?", tools=tools)

        assert reported_failures(tools) == [refusal(REFUSAL_FAILURE_MESSAGE)]
        assert reply.errors == [REFUSAL_FAILURE_MESSAGE]

    @pytest.mark.asyncio
    async def test_a_refused_first_prompt_leaves_the_next_turn_a_new_seeded_session(
        self,
    ) -> None:
        """ACP drops a refused prompt from the agent's context, so the session
        it was to seed is closed and the process's next turn starts over."""
        agent = FakeACPAgent().will_say(QUOTA_TEXT).stops_with(REFUSAL_STOP)
        async with acp_adapter(agent) as session:
            await session.send(
                "first?",
                bootstrap=True,
                history=rehydration_history(MARCO_REPLAY_LINE),
            )
            runtime = session.runtime()
            agent.stops_with(END_TURN_STOP)
            await session.send(QUESTION, room_context=[marco_message()])

            assert session.runtime() is runtime
            assert agent.lifecycle == []
            await session.retired_sessions_closed()
        refused, fresh = agent.session_ids()
        assert [prompt["session_id"] for prompt in agent.prompts] == [refused, fresh]
        assert agent.closed_sessions == [refused]
        assert seeded(agent.prompt_texts()[1])

    @pytest.mark.asyncio
    async def test_a_refused_first_prompt_leaves_nothing_to_resume_after_a_restart(
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
    async def test_a_refused_first_prompt_leaves_nothing_to_reload_after_recovery(
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
        it stays, so the session and its resume state are kept."""
        fake_agent.will_say(QUOTA_TEXT)
        async with acp_adapter(fake_agent) as session:
            await session.send("first")
            fake_agent.stops_with(REFUSAL_STOP)
            reply = await session.send_judged("second")
            kept = session.session_id("room-1")
            fake_agent.stops_with(END_TURN_STOP)
            await session.send("third")

        assert fake_agent.session_ids() == [kept]
        assert [prompt["session_id"] for prompt in fake_agent.prompts] == [kept] * 3
        assert fake_agent.closed_sessions == []
        assert reply.errors == [QUOTA_TEXT]
        assert reply.outline == ["error", "task"]
        assert SYSTEM_CONTEXT_HEADER not in fake_agent.prompt_texts()[2]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool", [BandTool.SEND_MESSAGE, BandTool.NO_REPLY])
    async def test_a_refusal_after_the_turn_replied_or_declined_completes_it(
        self, fake_agent: FakeACPAgent, tool: BandTool
    ) -> None:
        fake_agent.will_call_tool(
            "tc-1", **codex_mcp_call("band", tool), result="ok"
        ).will_say(QUOTA_TEXT).stops_with(REFUSAL_STOP)
        tools = TranscriptTools()
        async with acp_adapter(fake_agent) as session:
            reply = await session.send_judged("question?", tools=tools)

        assert reported_failures(tools) == []
        assert reply.outline == ["tool_call", "tool_result"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "stop_reason", [END_TURN_STOP, "max_tokens", "max_turn_requests"]
    )
    async def test_a_turn_not_refused_keeps_its_text_as_a_thought(
        self, fake_agent: FakeACPAgent, stop_reason: StopReason
    ) -> None:
        fake_agent.will_say(QUOTA_TEXT).stops_with(stop_reason)
        async with acp_adapter(fake_agent) as session:
            with pytest.raises(TurnResultAlreadyReported):
                await session.send_judged("question?")
            reply = session.last_reply

        assert reply.thoughts == [QUOTA_TEXT]
        assert reply.errors == [missing_reply_message()]
