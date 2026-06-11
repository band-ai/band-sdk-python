"""E2E tests for all standard adapters.

Verifies that each adapter can:
- Start, process a message, and stop against a real platform
- Execute platform tools (send_message)

Adapters tested: langgraph, anthropic, pydantic_ai, claude_sdk, opencode,
codex, letta. CrewAI, CrewAI Flow, and Parlant have dedicated E2E files because they
need separate dependency or adapter-specific lanes.

Run with:
    E2E_TESTS_ENABLED=true uv run pytest tests/e2e/adapters/ -v -s --no-cov

Run a specific adapter:
    E2E_TESTS_ENABLED=true uv run pytest tests/e2e/adapters/ -k langgraph -v -s --no-cov
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from thenvoi_rest import AsyncRestClient

from thenvoi.agent import Agent

from tests.e2e.adapters.conftest import AdapterFactory
from tests.e2e.conftest import E2EAgentCredentials, E2ESettings, requires_e2e
from tests.e2e.helpers import (
    TrackingWebSocketClient,
    run_smoke_test,
    run_tool_execution_test,
)


@pytest.mark.asyncio
@requires_e2e
class TestAdapterE2E:
    """E2E tests parametrized across all standard adapters."""

    @pytest.fixture
    async def running_agent(
        self,
        e2e_config: E2ESettings,
        adapter_entry: tuple[str, AdapterFactory],
        e2e_adapter_agent_credentials: E2EAgentCredentials,
    ) -> AsyncGenerator[tuple[str, Agent], None]:
        """Create and start an agent from the parametrized adapter factory.

        Yields (adapter_name, agent) with the agent running inside its
        async context manager. Ensures clean shutdown on exit.
        """
        adapter_name, factory = adapter_entry
        adapter = factory(e2e_config)

        agent = Agent.create(
            adapter=adapter,
            agent_id=e2e_adapter_agent_credentials.agent_id,
            api_key=e2e_adapter_agent_credentials.api_key,
            ws_url=e2e_config.thenvoi_ws_url,
            rest_url=e2e_config.thenvoi_base_url,
        )

        async with agent:
            yield adapter_name, agent

    @pytest.mark.flaky(reruns=2)
    async def test_smoke_responds_to_message(
        self,
        e2e_config: E2ESettings,
        e2e_adapter_room: tuple[str, str, str],
        e2e_adapter_agent_credentials: E2EAgentCredentials,
        ws_client: TrackingWebSocketClient,
        running_agent: tuple[str, Agent],
        api_client: AsyncRestClient,
    ):
        """Smoke test: agent starts, receives a message, and responds."""
        adapter_name, _agent = running_agent
        chat_id, _user_id, _user_name = e2e_adapter_room
        agent_id = e2e_adapter_agent_credentials.agent_id
        agent_name = e2e_adapter_agent_credentials.name

        await run_smoke_test(
            ws_client,
            api_client,
            chat_id,
            agent_name,
            agent_id,
            timeout=e2e_config.e2e_timeout,
            adapter_name=adapter_name,
        )

    @pytest.mark.flaky(reruns=2)
    async def test_tool_execution_send_message(
        self,
        e2e_config: E2ESettings,
        e2e_adapter_room: tuple[str, str, str],
        e2e_adapter_agent_credentials: E2EAgentCredentials,
        ws_client: TrackingWebSocketClient,
        running_agent: tuple[str, Agent],
        api_client: AsyncRestClient,
    ):
        """Verify the agent sends a visible chat response."""
        adapter_name, _agent = running_agent
        chat_id, _user_id, _user_name = e2e_adapter_room
        agent_id = e2e_adapter_agent_credentials.agent_id
        agent_name = e2e_adapter_agent_credentials.name

        await run_tool_execution_test(
            ws_client,
            api_client,
            chat_id,
            agent_name,
            agent_id,
            timeout=e2e_config.e2e_timeout,
            adapter_name=adapter_name,
        )
