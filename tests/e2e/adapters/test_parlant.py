"""E2E tests for the Parlant adapter.

Verifies that the Parlant adapter can:
- Start, process a message, and stop against a real platform
- Execute platform tools (send_message)

Note: Parlant requires a running Parlant server. These tests create
a server + agent in-process using the Parlant SDK.

Run with:
    E2E_TESTS_ENABLED=true uv run pytest tests/e2e/adapters/test_parlant.py -v -s --no-cov
"""

from __future__ import annotations

import importlib.util
from collections.abc import AsyncGenerator

import pytest
from band_rest import AsyncRestClient

from band.agent import Agent

from tests.e2e.settings import E2ESettings, requires_e2e
from tests.e2e.helpers import (
    TrackingWebSocketClient,
    run_smoke_test,
    run_tool_execution_test,
)

# The server lifecycle now lives in ``running_parlant_server`` (imported inside the
# fixture, after this gate), so no module-level ``parlant`` import is needed here.
HAS_PARLANT = importlib.util.find_spec("parlant.sdk") is not None

requires_parlant = pytest.mark.skipif(not HAS_PARLANT, reason="parlant not installed")


@pytest.mark.asyncio
@requires_e2e
@requires_parlant
class TestParlantE2E:
    """E2E tests specific to the Parlant adapter.

    These tests require Parlant to be installed and create an in-process
    Parlant server for each test.
    """

    @pytest.fixture
    async def running_parlant_agent(
        self,
        e2e_config: E2ESettings,
    ) -> AsyncGenerator[Agent, None]:
        """Create a Parlant adapter with an in-process server and start the agent.

        Yields a running Agent inside its async context manager. The server lifecycle
        goes through ``running_parlant_server``, which defaults to the OpenAI NLP
        service (authenticates with ``OPENAI_API_KEY``) on fresh ephemeral ports and
        tears the server down without hanging on Parlant's serve-forever
        ``__aexit__`` — see that helper for the mechanism.
        """
        from band.adapters.parlant import ParlantAdapter

        from tests.e2e.baseline.toolkit.parlant_server import running_parlant_server

        async with running_parlant_server() as server:
            parlant_agent = await server.create_agent(
                name="E2E Test Agent",
                description="A test agent for E2E validation. Keep responses short.",
            )

            adapter = ParlantAdapter(
                server=server,
                parlant_agent=parlant_agent,
                custom_section="Keep responses short and concise.",
            )

            agent = Agent.create(
                adapter=adapter,
                agent_id=e2e_config.test_agent_id,
                api_key=e2e_config.band_api_key,
                ws_url=e2e_config.band_ws_url,
                rest_url=e2e_config.band_base_url,
            )

            async with agent:
                yield agent

    @pytest.mark.flaky(reruns=2)
    async def test_smoke_responds_to_message(
        self,
        e2e_config: E2ESettings,
        e2e_parlant_room: tuple[str, str, str],
        e2e_agent_info: tuple[str, str],
        ws_client: TrackingWebSocketClient,
        running_parlant_agent: Agent,
        api_client: AsyncRestClient,
    ):
        """Smoke test: agent starts, receives a message, and responds."""
        chat_id, _user_id, _user_name = e2e_parlant_room
        agent_id, agent_name = e2e_agent_info

        await run_smoke_test(
            ws_client,
            api_client,
            chat_id,
            agent_name,
            agent_id,
            timeout=e2e_config.e2e_timeout,
            adapter_name="parlant",
        )

    @pytest.mark.flaky(reruns=2)
    async def test_tool_execution_send_message(
        self,
        e2e_config: E2ESettings,
        e2e_parlant_room: tuple[str, str, str],
        e2e_agent_info: tuple[str, str],
        ws_client: TrackingWebSocketClient,
        running_parlant_agent: Agent,
        api_client: AsyncRestClient,
    ):
        """Verify the agent uses band_send_message tool to respond."""
        chat_id, _user_id, _user_name = e2e_parlant_room
        agent_id, agent_name = e2e_agent_info

        await run_tool_execution_test(
            ws_client,
            api_client,
            chat_id,
            agent_name,
            agent_id,
            timeout=e2e_config.e2e_timeout,
            adapter_name="parlant",
        )
