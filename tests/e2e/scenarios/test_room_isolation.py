"""E2E tests for room isolation.

Tests that agents in different rooms don't see each other's context.
Each room maintains independent conversation history.

This verifies:
- Per-room state management in adapters
- Platform correctly scopes /context to individual rooms
- No cross-room leakage of conversation data

Run with:
    E2E_TESTS_ENABLED=true uv run pytest tests/e2e/scenarios/test_room_isolation.py -v -s --no-cov
"""

from __future__ import annotations

import logging
import uuid

import pytest
from band_rest import AsyncRestClient

from band.agent import Agent

from tests.e2e.adapters.conftest import AdapterFactory
from tests.e2e.settings import E2ESettings, RoomAllocator, requires_e2e
from tests.e2e.helpers import (
    TrackingWebSocketClient,
    assert_content_contains,
    assert_no_content_contains,
    listening_for_agent_responses,
    send_trigger_message,
)

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
@requires_e2e
class TestRoomIsolation:
    """Test that agents in different rooms maintain isolated context."""

    @pytest.mark.flaky(reruns=2)
    async def test_agents_in_different_rooms_isolated(
        self,
        e2e_config: E2ESettings,
        ws_client: TrackingWebSocketClient,
        adapter_entry: tuple[str, AdapterFactory],
        api_client: AsyncRestClient,
        e2e_fresh_room_allocator: RoomAllocator,
        e2e_agent_info: tuple[str, str],
    ):
        """Agents in different rooms don't see each other's context.

        Room A: Send "The code is <unique_a>"
        Room B: Send "The code is <unique_b>"
        Room A: Ask "What's the code?" -> Assert unique_a, not unique_b
        Room B: Ask "What's the code?" -> Assert unique_b, not unique_a

        Uses fresh rooms per run (via e2e_fresh_room_allocator) so no stale
        history accumulates — otherwise a reused room bloats the rehydrated
        context into timeouts or surfaces old codes on recall. Unique per-run
        keywords additionally guard against any cross-run confusion.
        """
        adapter_name, factory = adapter_entry
        timeout = e2e_config.e2e_timeout
        agent_id, agent_name = e2e_agent_info

        # Distinct keywords per room so the cross-room assertions can't pass by
        # coincidence; per-adapter/run suffix keeps logs unambiguous.
        run_id = uuid.uuid4().hex[:6]
        code_a = f"ALPHA_{adapter_name.upper()}_{run_id}"
        code_b = f"BRAVO_{adapter_name.upper()}_{run_id}"

        logger.info(
            "Testing room isolation with %s adapter (A=%s, B=%s)",
            adapter_name,
            code_a,
            code_b,
        )

        room_a_id, _ua, _na = await e2e_fresh_room_allocator("room-isolation-a")
        room_b_id, _ub, _nb = await e2e_fresh_room_allocator("room-isolation-b")
        logger.info("Room A: %s, Room B: %s", room_a_id, room_b_id)

        # Create adapter and agent (single agent, two rooms)
        adapter = factory(e2e_config)
        agent = Agent.create(
            adapter=adapter,
            agent_id=e2e_config.test_agent_id,
            api_key=e2e_config.band_api_key,
            ws_url=e2e_config.band_ws_url,
            rest_url=e2e_config.band_base_url,
        )

        async with agent:
            # --- Phase 1: Set context in both rooms sequentially ---
            # Sequential to avoid flakiness: a single agent processes one
            # room at a time, so concurrent sends can cause timeouts.
            async with listening_for_agent_responses(
                ws_client, room_a_id, timeout=timeout, raise_on_timeout=True
            ) as wait:
                await send_trigger_message(
                    api_client,
                    room_a_id,
                    f"Remember: the secret code is {code_a}. Confirm you remember it.",
                    agent_name,
                    agent_id,
                )
                room_a_phase1 = await wait()

            async with listening_for_agent_responses(
                ws_client, room_b_id, timeout=timeout, raise_on_timeout=True
            ) as wait:
                await send_trigger_message(
                    api_client,
                    room_b_id,
                    f"Remember: the secret code is {code_b}. Confirm you remember it.",
                    agent_name,
                    agent_id,
                )
                room_b_phase1 = await wait()

            logger.info(
                "[%s] Phase 1 complete: Room A got %d, Room B got %d response(s)",
                adapter_name,
                len(room_a_phase1),
                len(room_b_phase1),
            )

            # --- Phase 2: Query each room and verify isolation ---
            async with listening_for_agent_responses(
                ws_client, room_a_id, timeout=timeout, raise_on_timeout=True
            ) as wait:
                await send_trigger_message(
                    api_client,
                    room_a_id,
                    "What is the secret code? Reply with just the code word.",
                    agent_name,
                    agent_id,
                )
                room_a_received = await wait()

            async with listening_for_agent_responses(
                ws_client, room_b_id, timeout=timeout, raise_on_timeout=True
            ) as wait:
                await send_trigger_message(
                    api_client,
                    room_b_id,
                    "What is the secret code? Reply with just the code word.",
                    agent_name,
                    agent_id,
                )
                room_b_received = await wait()

            # Verify Room A knows code_a but not code_b
            assert_content_contains(room_a_received, code_a)
            assert_no_content_contains(room_a_received, code_b)

            # Verify Room B knows code_b but not code_a
            assert_content_contains(room_b_received, code_b)
            assert_no_content_contains(room_b_received, code_a)

        logger.info(
            "[%s] Room isolation test PASSED: rooms are correctly isolated",
            adapter_name,
        )
