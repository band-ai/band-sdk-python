"""E2E tests for context persistence across agent rejoin.

Tests that when an agent is removed from a room and re-added, it correctly
loads history from the platform and remembers prior context.

This verifies:
- is_session_bootstrap=True correctly loads history on rejoin
- The platform stores and returns conversation history via /context
- Adapters correctly convert and use historical messages

Run with:
    E2E_TESTS_ENABLED=true uv run pytest tests/e2e/scenarios/test_context_persistence.py -v -s --no-cov
"""

from __future__ import annotations

import logging
import uuid

import pytest
from band_rest import AsyncRestClient

from band.agent import Agent

from tests.e2e.adapters.conftest import AdapterFactory
from tests.e2e.conftest import E2ESettings, requires_e2e
from tests.e2e.helpers import (
    TrackingWebSocketClient,
    assert_content_contains,
    listening_for_agent_responses,
    send_trigger_message,
)

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
@requires_e2e
class TestContextPersistence:
    """Test that agents remember context after rejoin via platform history."""

    @pytest.mark.flaky(reruns=2)
    async def test_agent_remembers_context_after_rejoin(
        self,
        e2e_config: E2ESettings,
        e2e_adapter_room: tuple[str, str, str],
        e2e_agent_info: tuple[str, str],
        ws_client: TrackingWebSocketClient,
        adapter_entry: tuple[str, AdapterFactory],
        api_client: AsyncRestClient,
    ):
        """Agent removed then re-added remembers context via platform history.

        Phase 1: Send "Remember the code: <unique>", wait for acknowledgment
        Phase 2: Stop agent (triggers on_cleanup), restart (triggers bootstrap)
        Phase 3: Ask "What was the code?", assert response contains the unique code

        Uses a unique secret code per adapter to avoid cross-adapter contamination
        when sharing a room across parametrized runs.
        """
        adapter_name, factory = adapter_entry
        chat_id, _user_id, _user_name = e2e_adapter_room
        agent_id, agent_name = e2e_agent_info
        timeout = e2e_config.e2e_timeout
        # Unique code per adapter AND per run to prevent cross-run contamination
        # in shared rooms that persist across test sessions.
        run_id = uuid.uuid4().hex[:6]
        secret_code = f"CODE_{adapter_name.upper()}_{run_id}"

        logger.info(
            "Testing context persistence with %s adapter (code: %s)",
            adapter_name,
            secret_code,
        )

        # --- Phase 1: Establish context ---
        adapter = factory(e2e_config)
        agent = Agent.create(
            adapter=adapter,
            agent_id=e2e_config.test_agent_id,
            api_key=e2e_config.band_api_key,
            ws_url=e2e_config.band_ws_url,
            rest_url=e2e_config.band_base_url,
        )

        async with agent:
            async with listening_for_agent_responses(
                ws_client, chat_id, timeout=timeout, raise_on_timeout=True
            ) as wait:
                await send_trigger_message(
                    api_client,
                    chat_id,
                    f"Remember this secret code: {secret_code}. Respond confirming you remember it.",
                    agent_name,
                    agent_id,
                )
                phase1_received = await wait()

        logger.info(
            "[%s] Phase 1 complete: agent acknowledged with %d message(s)",
            adapter_name,
            len(phase1_received),
        )

        # --- Phase 2: Restart agent (new adapter instance, fresh state) ---
        # The old adapter is gone (on_cleanup was called). Create a fresh one.
        adapter2 = factory(e2e_config)
        agent2 = Agent.create(
            adapter=adapter2,
            agent_id=e2e_config.test_agent_id,
            api_key=e2e_config.band_api_key,
            ws_url=e2e_config.band_ws_url,
            rest_url=e2e_config.band_base_url,
        )

        async with agent2:
            async with listening_for_agent_responses(
                ws_client, chat_id, timeout=timeout, raise_on_timeout=True
            ) as wait:
                await send_trigger_message(
                    api_client,
                    chat_id,
                    "What was the secret code I told you to remember? Reply with just the code.",
                    agent_name,
                    agent_id,
                )
                phase2_received = await wait()

            assert_content_contains(phase2_received, secret_code)

        logger.info(
            "[%s] Context persistence test PASSED: agent remembered %s",
            adapter_name,
            secret_code,
        )
