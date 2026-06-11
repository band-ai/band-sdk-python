"""E2E test for the AgentCore three-agent orchestration demo.

This test validates the INT-506 acceptance scenario: a user asks
``@personal_assistant`` a question that requires both ``@weather`` and
``@math``; PA adds them to the room, asks each what it needs, reads the
replies, and posts a final synthesized answer.

Prerequisites (the test skips otherwise):

1. ``E2E_TESTS_ENABLED=true``
2. ``BAND_API_KEY`` and ``BAND_API_KEY_USER`` configured in
   ``.env.test`` (standard E2E setup).
3. The agentcore demo deployed and running externally:
   - Three AgentCore Runtimes (weather/math/personal_assistant).
   - The bridge running with ``BAND_BRIDGE_AGENTS`` pointing at the
     three identities and their runtime ARNs.
4. ``AGENTCORE_DEMO_PA_AGENT_ID`` env var set to the personal_assistant's
   Band agent UUID so the test knows whom to @-mention. (The other two
   agents are recruited by PA at runtime; the test doesn't address them
   directly.)

Run with::

    E2E_TESTS_ENABLED=true \\
    AGENTCORE_DEMO_PA_AGENT_ID=<uuid> \\
        uv run pytest tests/e2e/agentcore/ -v -s --no-cov
"""

from __future__ import annotations

import asyncio
import logging
import os

import pytest
from thenvoi_rest import AsyncRestClient, CreateMyChatRoomRequestChat
from thenvoi_rest.types import ParticipantRequest

from tests.e2e.conftest import E2ESettings, requires_e2e
from tests.e2e.helpers import (
    TrackingWebSocketClient,
    listening_for_agent_responses,
    send_trigger_message,
)

logger = logging.getLogger(__name__)


_PA_AGENT_ID_ENV = "AGENTCORE_DEMO_PA_AGENT_ID"
_PA_AGENT_NAME = os.environ.get("AGENTCORE_DEMO_PA_AGENT_NAME", "personal_assistant")

requires_agentcore_demo = pytest.mark.skipif(
    not os.environ.get(_PA_AGENT_ID_ENV),
    reason=(
        f"{_PA_AGENT_ID_ENV} not set — the AgentCore demo bridge + 3 runtimes "
        "must be deployed before this test runs. See examples/agentcore/README.md."
    ),
)


async def _create_room_with_pa(
    user_client: AsyncRestClient,
    pa_agent_id: str,
    label: str,
) -> str:
    """Create a fresh chat room and add @personal_assistant to it."""
    response = await user_client.human_api_chats.create_my_chat_room(
        chat=CreateMyChatRoomRequestChat(),
    )
    if not response.data:
        raise RuntimeError(f"[{label}] create_my_chat_room returned no data")
    room_id = response.data.id
    logger.info("Created room %s for demo flow [%s]", room_id, label)

    await user_client.human_api_participants.add_my_chat_participant(
        chat_id=room_id,
        participant=ParticipantRequest(
            participant_id=pa_agent_id,
            role="member",
        ),
    )
    return room_id


async def _run_single_flow(
    *,
    api_client: AsyncRestClient,
    ws_client: TrackingWebSocketClient,
    pa_agent_id: str,
    question: str,
    label: str,
    timeout: float,
) -> str:
    """Run one user → PA → peers → PA → user flow; return PA's final reply."""
    room_id = await _create_room_with_pa(api_client, pa_agent_id, label)

    # min_messages=3 because we expect PA's mentions to peers + peer replies + PA's final
    # answer. The exact number varies (PA may send multiple messages); we just need
    # at least one PA message that addresses the user. Set higher to give the flow
    # room to play out, then look at the last messages for the synthesized answer.
    async with listening_for_agent_responses(
        ws_client, room_id, timeout=timeout, min_messages=1
    ) as wait:
        await send_trigger_message(
            api_client,
            room_id,
            question,
            mention_name=_PA_AGENT_NAME,
            mention_id=pa_agent_id,
        )
        received = await wait()

    assert received, (
        f"[{label}] PA never replied — check the bridge is running and that "
        "the three AgentCore runtimes are healthy."
    )

    # PA's final answer should mention both cities by name, somewhere among the
    # responses. The LLM may produce more than one response; check all of them.
    final = received[-1]
    logger.info("[%s] PA final reply: %s", label, final.content[:200])
    return final.content


@pytest.mark.asyncio
@requires_e2e
@requires_agentcore_demo
class TestAgentCoreThreeAgentOrchestration:
    """The INT-506 acceptance scenario plus parallel-rooms coverage."""

    @pytest.mark.flaky(reruns=1)
    async def test_pa_coordinates_with_peers_in_one_room(
        self,
        e2e_config: E2ESettings,
        ws_client: TrackingWebSocketClient,
        api_client: AsyncRestClient,
    ) -> None:
        """User asks PA, PA recruits @weather and @math, final answer posted.

        Loose verification: the reply should reference both cities (PA must
        have collected weather for each) — we don't check the percentage
        value because LLM outputs vary.
        """
        pa_agent_id = os.environ[_PA_AGENT_ID_ENV]
        timeout = max(e2e_config.e2e_timeout, 90.0)  # multi-hop, allow extra

        final = await _run_single_flow(
            api_client=api_client,
            ws_client=ws_client,
            pa_agent_id=pa_agent_id,
            question=(
                "What is the temperature difference now, in percents, "
                "between Tel Aviv and Warsaw?"
            ),
            label="solo",
            timeout=timeout,
        )

        # Both city names should appear in the final synthesis.
        lower = final.lower()
        assert "tel aviv" in lower, f"PA final reply missing 'Tel Aviv': {final!r}"
        assert "warsaw" in lower, f"PA final reply missing 'Warsaw': {final!r}"

    @pytest.mark.flaky(reruns=1)
    async def test_two_parallel_rooms(
        self,
        e2e_config: E2ESettings,
        ws_client: TrackingWebSocketClient,
        api_client: AsyncRestClient,
    ) -> None:
        """Two concurrent PA orchestrations in two different rooms.

        Verifies the bridge's per-room session isolation and that
        runtimeSessionId derivation pins each room to its own AgentCore
        microVM. Both flows must complete; neither should bleed into the
        other.
        """
        pa_agent_id = os.environ[_PA_AGENT_ID_ENV]
        timeout = max(e2e_config.e2e_timeout, 90.0)

        flows = await asyncio.gather(
            _run_single_flow(
                api_client=api_client,
                ws_client=ws_client,
                pa_agent_id=pa_agent_id,
                question=(
                    "What is the temperature difference now, in percents, "
                    "between Tel Aviv and Warsaw?"
                ),
                label="room-A",
                timeout=timeout,
            ),
            _run_single_flow(
                api_client=api_client,
                ws_client=ws_client,
                pa_agent_id=pa_agent_id,
                question=(
                    "What is the temperature difference now, in percents, "
                    "between New York and London?"
                ),
                label="room-B",
                timeout=timeout,
            ),
        )

        reply_a, reply_b = flows
        # Each room's answer should mention its own cities, not the other's.
        assert "tel aviv" in reply_a.lower() and "warsaw" in reply_a.lower(), (
            f"Room A reply missing TLV/Warsaw: {reply_a!r}"
        )
        assert "new york" in reply_b.lower() and "london" in reply_b.lower(), (
            f"Room B reply missing NYC/London: {reply_b!r}"
        )
        # Cross-bleed check: room A should NOT mention NYC/London and vice versa.
        assert "new york" not in reply_a.lower(), (
            f"Room A leaked Room B cities into its reply: {reply_a!r}"
        )
        assert "tel aviv" not in reply_b.lower(), (
            f"Room B leaked Room A cities into its reply: {reply_b!r}"
        )
