"""A prompt the agent refuses fails its turn unless the turn already replied."""

from __future__ import annotations

from typing import get_args

import pytest
from acp.schema import StopReason
from band_sdk_core import AgentFailure, missing_reply_message

from band.core.protocols import TurnResultAlreadyReported
from band.core.types import Emit
from band.integrations.acp.types import (
    ACP_FAILURE_PROVIDER,
    REFUSAL_FAILURE_CODE,
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
from tests.integrations.acp.clientbehavior.kit import QUOTA_TEXT, codex_mcp_call


def refusal(message: str) -> dict[str, object]:
    """A refused turn's reported failure, as ``reported_failures`` lists it."""
    return AgentFailure(ACP_FAILURE_PROVIDER, message, REFUSAL_FAILURE_CODE).to_dict()


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
    @pytest.mark.parametrize("tool", [BandTool.SEND_MESSAGE, BandTool.NO_REPLY])
    async def test_a_refusal_after_the_turn_replied_or_declined_completes_it(
        self, fake_agent: FakeACPAgent, tool: BandTool
    ) -> None:
        """The turn is complete, but its refused prompt still never seeded
        the session, so that session is retired all the same."""
        fake_agent.will_call_tool(
            "tc-1", **codex_mcp_call("band", tool), result="ok"
        ).will_say(QUOTA_TEXT).stops_with(REFUSAL_STOP)
        tools = TranscriptTools()
        async with acp_adapter(fake_agent) as session:
            reply = await session.send_judged("question?", tools=tools)
            await session.retired_sessions_closed()

        assert reported_failures(tools) == []
        assert reply.outline == ["tool_call", "tool_result"]
        assert fake_agent.closed_sessions == fake_agent.session_ids()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "stop_reason", sorted(set(get_args(StopReason)) - {REFUSAL_STOP})
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
