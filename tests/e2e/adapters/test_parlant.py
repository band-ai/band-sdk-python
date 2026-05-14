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

from collections.abc import AsyncGenerator
import asyncio
import contextlib
import os
import socket

import pytest
from thenvoi_rest import AsyncRestClient

from thenvoi.agent import Agent

from tests.e2e.conftest import E2ESettings, requires_e2e
from tests.e2e.helpers import (
    TrackingWebSocketClient,
    assert_content_contains,
    listening_for_agent_responses,
    run_smoke_test,
    send_trigger_message,
)

try:
    import parlant.sdk as p

    HAS_PARLANT = True
except ImportError:
    HAS_PARLANT = False

requires_parlant = pytest.mark.skipif(not HAS_PARLANT, reason="parlant not installed")


def _configure_parlant_agent_credentials() -> None:
    """Use the local Parlant agent config when a sourced env exposes a user key."""
    current_key = os.getenv("THENVOI_API_KEY", "")
    if current_key.startswith(("thnv_a", "band_a")) and os.getenv("TEST_AGENT_ID"):
        return

    if current_key.startswith(("thnv_u", "band_u")) and not os.getenv(
        "THENVOI_API_KEY_USER"
    ):
        os.environ["THENVOI_API_KEY_USER"] = current_key

    try:
        from thenvoi.config import load_agent_config

        agent_id, api_key = load_agent_config("tom_agent")
    except (FileNotFoundError, ValueError):
        return

    os.environ["THENVOI_API_KEY"] = api_key
    os.environ["TEST_AGENT_ID"] = agent_id
    os.environ.setdefault("THENVOI_AGENT_ID", agent_id)


_configure_parlant_agent_credentials()


def _unused_local_port() -> int:
    """Reserve a free localhost port for a short-lived in-process Parlant server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


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

        Yields a running Agent inside its async context manager.
        """
        from thenvoi.adapters.parlant import ParlantAdapter
        from thenvoi.integrations.parlant.tools import create_parlant_tools

        if not os.getenv("OPENAI_API_KEY"):
            pytest.skip("OPENAI_API_KEY is required for Parlant E2E tests")

        server = p.Server(
            host="127.0.0.1",
            port=_unused_local_port(),
            tool_service_port=_unused_local_port(),
            nlp_service=p.NLPServices.openai,
        )
        await server.__aenter__()
        try:
            parlant_tools = create_parlant_tools()
            parlant_agent = await server.create_agent(
                name="E2E Test Agent",
                description=(
                    "A test agent for E2E validation. Keep responses short. "
                    "Incoming messages start with a mention to you; treat that as "
                    "the trigger target, not the reply recipient. Reply to the "
                    "user who sent the message."
                ),
            )
            await parlant_agent.create_guideline(
                condition="User asks you to reply with a specific word or phrase",
                action=(
                    "Call thenvoi_send_message with the requested word or phrase "
                    "as content, and set mentions to the user's name or handle. "
                    "Do not address or mention yourself."
                ),
                tools=parlant_tools,
            )

            adapter = ParlantAdapter(
                server=server,
                parlant_agent=parlant_agent,
                custom_section="Keep responses short and concise.",
            )

            agent = Agent.create(
                adapter=adapter,
                agent_id=e2e_config.test_agent_id,
                api_key=e2e_config.thenvoi_api_key,
                ws_url=e2e_config.thenvoi_ws_url,
                rest_url=e2e_config.thenvoi_base_url,
            )

            async with agent:
                yield agent
        finally:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(server.__aexit__(None, None, None), timeout=30)

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

    async def test_tool_execution_send_message(
        self,
        e2e_config: E2ESettings,
        e2e_parlant_room: tuple[str, str, str],
        e2e_agent_info: tuple[str, str],
        ws_client: TrackingWebSocketClient,
        running_parlant_agent: Agent,
        api_client: AsyncRestClient,
    ):
        """Verify the agent uses thenvoi_send_message tool to respond."""
        chat_id, _user_id, user_name = e2e_parlant_room
        agent_id, agent_name = e2e_agent_info

        async with listening_for_agent_responses(
            ws_client, chat_id, timeout=e2e_config.e2e_timeout
        ) as wait:
            await send_trigger_message(
                api_client,
                chat_id,
                f"Reply to {user_name} with the word PINEAPPLE. Do not reply to {agent_name}.",
                agent_name,
                agent_id,
            )
            received = await wait()

        assert len(received) > 0, "[parlant] Agent should have sent a message via tool"
        assert_content_contains(received, "PINEAPPLE")
