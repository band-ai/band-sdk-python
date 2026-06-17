"""Agno thought-emission E2E test against the live Band platform.

The generic smoke and tool-execution tests run for Agno via the parametrized
suite in ``adapters/test_all_adapters.py``. This module covers behavior unique
to the Agno adapter: emitting agent reasoning as ``thought`` events when
``Emit.THOUGHTS`` is enabled.

Observability note (verified against the live platform): agent-emitted events
(``thought``, ``tool_call``, ``tool_result``) are returned by the
``agent_api_context`` REST endpoint but are NOT delivered over the user's
WebSocket ``message_created`` stream, which carries only ``text``. So this test
synchronizes on the agent's ``text`` reply over the socket, then asserts the
``thought`` event via a direct REST query.

Run with:
    E2E_TESTS_ENABLED=true uv run pytest \
        tests/e2e/scenarios/agno/test_thoughts.py -v -s --no-cov --log-cli-level=INFO
"""

from __future__ import annotations

import logging

import pytest
from band_rest import AsyncRestClient

from tests.e2e.conftest import E2ESettings, requires_e2e
from tests.e2e.helpers import (
    TrackingWebSocketClient,
    listening_for_room_activity,
    log_banner,
    log_step,
    send_trigger_message,
)
from tests.e2e.scenarios.agno.conftest import (
    assert_thought_emitted,
    build_thinking_adapter,
    running_agent,
)

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
@requires_e2e
class TestAgnoThoughts:
    """Verify the Agno adapter emits reasoning as thought events."""

    @pytest.mark.flaky(reruns=2)
    async def test_agent_emits_thought_events(
        self,
        e2e_config: E2ESettings,
        agno_thoughts_room: tuple[str, str, str],
        e2e_agent_info: tuple[str, str],
        e2e_session_client: AsyncRestClient,
        ws_client: TrackingWebSocketClient,
        api_client: AsyncRestClient,
    ) -> None:
        """A reasoning Agno agent posts at least one ``thought`` event.

        Synchronizes on the agent's text reply over WebSocket (the reliable
        "turn finished" signal), then asserts the thought event via REST.
        """
        room_id, _user_id, _user_name = agno_thoughts_room
        agent_id, agent_name = e2e_agent_info
        timeout = min(float(e2e_config.e2e_timeout) * 2, 90.0)

        log_banner("Scenario 3: Agno thought emission")
        log_step(1, f"starting reasoning agent {agent_name}")

        adapter = build_thinking_adapter(e2e_config)

        async with running_agent(
            adapter,
            agent_id=e2e_config.test_agent_id,
            api_key=e2e_config.band_api_key,
            config=e2e_config,
        ):
            log_step(2, "asking a question that requires step-by-step reasoning")
            # Wait for the agent's text reply (events don't arrive over WS).
            async with listening_for_room_activity(
                ws_client,
                room_id,
                message_types=("text",),
                sender_id=agent_id,
                timeout=timeout,
                raise_on_timeout=True,
            ) as wait_for_reply:
                await send_trigger_message(
                    api_client,
                    room_id,
                    (
                        "If a basket has 3 apples and I add 2 more bags with 4 "
                        "apples each, how many apples are there in total? Think "
                        "it through step by step, then give the number."
                    ),
                    agent_name,
                    agent_id,
                )
                await wait_for_reply()

            log_step(3, "agent replied; verifying a thought event via REST")
            await assert_thought_emitted(e2e_session_client, room_id)

        log_banner("Scenario 3 PASSED")
