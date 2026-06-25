"""Mutual-greeting smoke across two real agents (LangGraph + Anthropic).

Exercises the wait primitives and the minimal LLM judge end to end: mint a
LangGraph agent and an Anthropic agent into one room, have the user ask each to
greet the other, capture the turns with the event-driven waiter, settle the
room with the token-barrier drain, and let the judge validate that a mutual
greeting actually happened. Validates the tools, not an L-level contract.
"""

from __future__ import annotations

import contextlib

import pytest

from band.adapters.anthropic import AnthropicAdapter
from band.adapters.langgraph import LangGraphAdapter
from band.client.streaming import MessageCreatedPayload

from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.tools.judge import judge
from tests.e2e.baseline.tools.provisioning import (
    ResourceManager,
    running_minted_agent,
)
from tests.e2e.baseline.tools.user_ops import UserOps
from tests.e2e.baseline.tools.waiting import drain, reply_capture
from tests.e2e.conftest import requires_e2e
from tests.e2e.helpers import TrackingWebSocketClient

_SHORT = "Keep responses to one short sentence. Always reply using band_send_message."


def _build_langgraph(settings: BaselineSettings) -> LangGraphAdapter:
    from langchain_openai import ChatOpenAI
    from langgraph.checkpoint.memory import MemorySaver

    return LangGraphAdapter(
        llm=ChatOpenAI(
            model=settings.llm_models.openai_model,
            api_key=settings.llm_credentials.openai_api_key,
        ),
        checkpointer=MemorySaver(),
        custom_section=_SHORT,
    )


def _build_anthropic(settings: BaselineSettings) -> AnthropicAdapter:
    return AnthropicAdapter(
        model=settings.llm_models.anthropic_model,
        provider_key=settings.llm_credentials.anthropic_api_key,
        custom_section=_SHORT,
    )


def _transcript(messages: list[MessageCreatedPayload]) -> str:
    return "\n".join(f"[{m.sender_name or m.sender_id}]: {m.content}" for m in messages)


@requires_e2e
@pytest.mark.asyncio(loop_scope="session")
async def test_two_agents_greet_each_other(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    baseline_ws: TrackingWebSocketClient,
) -> None:
    if not baseline_settings.llm_credentials.openai_api_key:
        pytest.skip("OPENAI_API_KEY not set (needed for the LangGraph agent)")
    if not baseline_settings.llm_credentials.anthropic_api_key:
        pytest.skip("ANTHROPIC_API_KEY not set (needed for the Anthropic agent)")

    async with contextlib.AsyncExitStack() as stack:
        _, a = await stack.enter_async_context(
            running_minted_agent(
                _build_langgraph(baseline_settings), resource_manager, label="lg"
            )
        )
        _, b = await stack.enter_async_context(
            running_minted_agent(
                _build_anthropic(baseline_settings), resource_manager, label="anthropic"
            )
        )

        room_id = await resource_manager.mint_room(
            title="e2e-mutual-greeting", participants=[a.id, b.id]
        )

        async with reply_capture(baseline_ws, room_id) as capture:
            # User asks each agent, in turn, to greet the other.
            await user_ops.send_message(
                room_id,
                f"please say hello to {b.name}",
                mention_id=a.id,
                mention_name=a.name,
            )
            await capture.wait_until(
                lambda msgs: any(m.sender_id == a.id for m in msgs)
            )

            await user_ops.send_message(
                room_id,
                f"please say hello to {a.name}",
                mention_id=b.id,
                mention_name=b.name,
            )
            await capture.wait_until(
                lambda msgs: any(m.sender_id == b.id for m in msgs)
            )

            # Settle the room deterministically before judging.
            await drain(
                capture, user_ops, room_id, mention_id=a.id, mention_name=a.name
            )

            transcript = _transcript(capture.messages)

    verdict = await judge(
        criteria=(
            "Two agents share a room. The transcript should show BOTH agents "
            "producing a greeting (e.g. 'hello', 'hi') directed at the other. "
            "Pass only if both agents greeted."
        ),
        transcript=transcript,
        model=baseline_settings.llm_models.judge_model,
        api_key=baseline_settings.llm_credentials.anthropic_api_key,
    )
    assert verdict.passed, (
        f"Judge failed mutual greeting: {verdict.reason}\n{transcript}"
    )
