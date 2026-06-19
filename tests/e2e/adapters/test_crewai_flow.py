"""E2E tests for the CrewAI Flow adapter.

CrewAI Flow is not part of the shared LLM adapter E2E matrix because it does not
claim generic prompt-memory behavior. Its N-A compensation is narrower: a Flow
terminal decision must become a visible Band message through the adapter's real
``SideEffectExecutor`` path.

Run with:
    E2E_TESTS_ENABLED=true uv run pytest tests/e2e/adapters/test_crewai_flow.py -v -s --no-cov
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from band_rest import AsyncRestClient

from band.agent import Agent

from tests.e2e.conftest import E2ESettings, RoomAllocator, requires_e2e
from tests.e2e.helpers import (
    TrackingWebSocketClient,
    assert_content_contains,
    listening_for_agent_responses,
    send_trigger_message,
)


@pytest.fixture
async def e2e_crewai_flow_room(
    e2e_room_allocator: RoomAllocator,
) -> tuple[str, str, str]:
    """Dedicated room for CrewAI Flow E2E tests."""
    return await e2e_room_allocator("crewai_flow")


@pytest.mark.asyncio
@requires_e2e
async def test_crewai_flow_terminal_decision_sends_visible_message(
    e2e_config: E2ESettings,
    e2e_crewai_flow_room: tuple[str, str, str],
    e2e_agent_info: tuple[str, str],
    ws_client: TrackingWebSocketClient,
    api_client: AsyncRestClient,
) -> None:
    """A terminal Flow return is executed through the real side-effect sender."""
    from band.adapters.crewai_flow import CrewAIFlowAdapter

    validation_code = f"CREWAI_FLOW_{uuid.uuid4().hex[:8]}"

    class _TerminalResponseFlow:
        async def kickoff_async(self, inputs: dict[str, Any]) -> dict[str, Any]:
            return {
                "decision": "direct_response",
                "content": validation_code,
                "mentions": [],
            }

    adapter = CrewAIFlowAdapter(flow_factory=_TerminalResponseFlow)
    agent = Agent.create(
        adapter=adapter,
        agent_id=e2e_config.test_agent_id,
        api_key=e2e_config.band_api_key,
        ws_url=e2e_config.band_ws_url,
        rest_url=e2e_config.band_base_url,
    )

    chat_id, _user_id, _user_name = e2e_crewai_flow_room
    agent_id, agent_name = e2e_agent_info

    async with agent:
        async with listening_for_agent_responses(
            ws_client,
            chat_id,
            timeout=e2e_config.e2e_timeout,
            raise_on_timeout=True,
            expected_agent_id=agent_id,
        ) as wait:
            await send_trigger_message(
                api_client,
                chat_id,
                f"Return the terminal Flow validation code {validation_code}.",
                agent_name,
                agent_id,
            )
            received = await wait()

    assert_content_contains(received, validation_code)
