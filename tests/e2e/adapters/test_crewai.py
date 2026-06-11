"""E2E tests for the CrewAI adapter.

CrewAI runs in the separate ``dev-crewai`` dependency lane because it cannot
coexist with the default dev lane's Parlant and Pydantic AI dependencies.

Run with:
    uv sync --extra dev-crewai
    E2E_TESTS_ENABLED=true uv run pytest tests/e2e/adapters/test_crewai.py -v -s --no-cov
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from thenvoi_rest import AsyncRestClient

from thenvoi.agent import Agent

from tests.e2e.adapters.conftest import create_crewai_adapter
from tests.e2e.conftest import E2ESettings, RoomAllocator, requires_e2e
from tests.e2e.helpers import (
    TrackingWebSocketClient,
    run_smoke_test,
    run_tool_execution_test,
)


@pytest.fixture
async def e2e_crewai_room(
    e2e_room_allocator: RoomAllocator,
) -> tuple[str, str, str]:
    """Dedicated room for CrewAI adapter tests."""
    return await e2e_room_allocator("crewai")


@pytest.fixture
async def running_crewai_agent(
    e2e_config: E2ESettings,
) -> AsyncGenerator[Agent, None]:
    """Create and start a CrewAI agent in the dedicated dependency lane."""
    adapter = create_crewai_adapter(e2e_config)
    agent = Agent.create(
        adapter=adapter,
        agent_id=e2e_config.test_agent_id,
        api_key=e2e_config.thenvoi_api_key,
        ws_url=e2e_config.thenvoi_ws_url,
        rest_url=e2e_config.thenvoi_base_url,
    )

    async with agent:
        yield agent


@pytest.mark.asyncio
@requires_e2e
class TestCrewAIE2E:
    """CrewAI-specific E2E coverage for the separate dependency lane."""

    async def test_smoke_responds_to_message(
        self,
        e2e_config: E2ESettings,
        e2e_crewai_room: tuple[str, str, str],
        e2e_agent_info: tuple[str, str],
        ws_client: TrackingWebSocketClient,
        running_crewai_agent: Agent,
        api_client: AsyncRestClient,
    ) -> None:
        """Smoke test: agent starts, receives a message, and responds."""
        chat_id, _user_id, _user_name = e2e_crewai_room
        agent_id, agent_name = e2e_agent_info

        await run_smoke_test(
            ws_client,
            api_client,
            chat_id,
            agent_name,
            agent_id,
            timeout=e2e_config.e2e_timeout,
            adapter_name="crewai",
        )

    async def test_tool_execution_send_message(
        self,
        e2e_config: E2ESettings,
        e2e_crewai_room: tuple[str, str, str],
        e2e_agent_info: tuple[str, str],
        ws_client: TrackingWebSocketClient,
        running_crewai_agent: Agent,
        api_client: AsyncRestClient,
    ) -> None:
        """Verify CrewAI uses thenvoi_send_message to respond."""
        chat_id, _user_id, _user_name = e2e_crewai_room
        agent_id, agent_name = e2e_agent_info

        await run_tool_execution_test(
            ws_client,
            api_client,
            chat_id,
            agent_name,
            agent_id,
            timeout=e2e_config.e2e_timeout,
            adapter_name="crewai",
        )
