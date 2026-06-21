"""E2E tests for multi-agent orchestration with the Agno adapter.

Two real Agno agents and a user collaborate against the live Band platform:

- **Agent A** (assistant): chats with the user about a grocery list. It cannot
  do arithmetic, so it invites a calculator agent, asks it for the total, and
  removes it when done.
- **Agent B** (calculator): owns a native ``add_numbers`` tool and reports its
  executions via ``Emit.EXECUTION`` so the test can verify (by direct REST
  query) that the tool actually ran.

Scenario 1 (``test_assistant_invites_calculator_for_total``) runs the flow
straight through. Scenario 2 (``test_multi_agent_survives_restart``) kills and
restarts an agent mid-conversation to verify history rehydration, parametrized
over which agent restarts: A, B, or both.

Requires a second provisioned agent (``BAND_API_KEY_2`` / ``TEST_AGENT_ID_2``)
that is discoverable by the first. Tests skip cleanly when it is absent.

Run with:
    E2E_TESTS_ENABLED=true uv run pytest \
        tests/e2e/scenarios/agno/test_multi_agent.py -v -s --no-cov
"""

from __future__ import annotations

import asyncio
import logging
import uuid

import pytest
from band_rest import AsyncRestClient

from tests.e2e.settings import E2ESettings, RoomAllocator, requires_e2e
from tests.e2e.helpers import (
    TrackingWebSocketClient,
    listening_for_room_activity,
    log_banner,
    log_step,
    running_agent,
    send_trigger_message,
)
from tests.e2e.scenarios.agno.conftest import (
    GROCERY_TOTAL,
    assert_calculator_ran,
    assert_total_reported,
    build_assistant_adapter,
    create_calculator_agno_adapter,
    grocery_list_text,
    participant_present,
    wait_participant_absent,
)

logger = logging.getLogger(__name__)

# A restarted agent reopens a WebSocket for an agent_id whose previous connection
# just closed. Reconnecting within the platform's supersede window returns HTTP
# 429, so pause briefly between a restart's kill and reconnect to let the old
# connection tear down and the rate-limit window clear.
_RESTART_RECONNECT_DELAY_S = 5.0


@pytest.mark.asyncio
@requires_e2e
class TestAgnoMultiAgent:
    """Multi-agent orchestration and rehydration tests for the Agno adapter."""

    @pytest.mark.flaky(reruns=2)
    @pytest.mark.timeout(300)
    async def test_assistant_invites_calculator_for_total(
        self,
        e2e_config: E2ESettings,
        e2e_room_allocator: RoomAllocator,
        e2e_agent_info: tuple[str, str],
        e2e_agent_info_2: tuple[str, str],
        e2e_session_client: AsyncRestClient,
        e2e_session_client_2: AsyncRestClient,
        ws_client: TrackingWebSocketClient,
        api_client: AsyncRestClient,
    ) -> None:
        """Assistant brings in a calculator agent to total a grocery list.

        Verifies (by direct REST query) that the calculator's tool ran, the
        total was reported, and the calculator was removed afterward.
        """
        # Reusing allocator (cached by name): both multi-agent tests share one
        # room. Starts with Agent A + User; Agent B joins during the flow.
        room_id, _user_id, _user_name = await e2e_room_allocator("agno_multi_agent")
        agent_a_id, agent_a_name = e2e_agent_info
        agent_b_id, agent_b_name = e2e_agent_info_2
        run_id = uuid.uuid4().hex[:6]
        # Long wait: A must invite B, B must run + reply, A must relay + remove.
        flow_timeout = min(float(e2e_config.e2e_timeout) * 3, 100.0)

        log_banner(f"Scenario 1: assistant invites calculator (run {run_id})")
        log_step(
            1, f"cast: {agent_a_name} (assistant) + {agent_b_name} (calculator) + user"
        )

        assistant = build_assistant_adapter(
            e2e_config,
            calculator_id=agent_b_id,
            calculator_name=agent_b_name,
        )
        calculator = create_calculator_agno_adapter(e2e_config)

        async with (
            running_agent(
                assistant,
                agent_id=e2e_config.test_agent_id,
                api_key=e2e_config.band_api_key,
                config=e2e_config,
            ),
            running_agent(
                calculator,
                agent_id=e2e_config.test_agent_id_2,
                api_key=e2e_config.band_api_key_2,
                config=e2e_config,
            ),
        ):
            log_step(
                2,
                f"user → {agent_a_name}: grocery list [{grocery_list_text()}], "
                f"asks for total (expect ${GROCERY_TOTAL:.2f})",
            )
            prompt = (
                f"(run {run_id}) Here is my grocery list with prices: "
                f"{grocery_list_text()}. What's the total? You can't do math "
                "yourself, so bring in the calculator agent to add it up, then "
                "remove them once you have the answer."
            )
            # Wait until the calculator posts its total (a text message from B).
            async with listening_for_room_activity(
                ws_client,
                room_id,
                message_types=("text",),
                sender_id=agent_b_id,
                timeout=flow_timeout,
            ) as wait_for_calculator:
                await send_trigger_message(
                    api_client, room_id, prompt, agent_a_name, agent_a_id
                )
                calc_messages = await wait_for_calculator()

            log_step(
                3,
                f"{agent_b_name} replied ({len(calc_messages)} msg); verifying via REST",
            )
            # Primary: the calculator's add_numbers tool actually ran.
            await assert_calculator_ran(e2e_session_client_2, room_id)
            # Secondary: the total reached the room.
            await assert_total_reported(e2e_session_client, room_id)

            log_step(4, f"checking {agent_a_name} removed {agent_b_name}")
            removed = await wait_participant_absent(
                e2e_session_client, room_id, agent_b_id, timeout=flow_timeout / 2
            )
            assert removed, (
                f"Calculator agent {agent_b_name} ({agent_b_id}) was still a "
                f"participant of room {room_id} after the flow completed; the "
                "assistant did not remove it."
            )
            log_step("assert", "calculator removed from room")

        log_banner(f"Scenario 1 PASSED (run {run_id})")

    @pytest.mark.flaky(reruns=2)
    @pytest.mark.timeout(300)
    @pytest.mark.parametrize("restart_target", ["A", "B", "both"])
    async def test_multi_agent_survives_restart(
        self,
        restart_target: str,
        e2e_config: E2ESettings,
        e2e_room_allocator: RoomAllocator,
        e2e_agent_info: tuple[str, str],
        e2e_agent_info_2: tuple[str, str],
        e2e_session_client: AsyncRestClient,
        e2e_session_client_2: AsyncRestClient,
        ws_client: TrackingWebSocketClient,
        api_client: AsyncRestClient,
    ) -> None:
        """Same flow with an agent killed and restarted mid-conversation.

        The restarted agent must rehydrate prior conversation from platform
        history (``is_session_bootstrap``) and continue correctly.

        - target ``A``: restart the assistant after it has the grocery list,
          before it computes the total. A must recall the list post-restart.
        - target ``B``: restart the calculator after it has joined and summed
          once, then have it recompute. B must rehydrate the conversation.
        - target ``both``: restart A (then continue) and later B.
        """
        # Reusing allocator (cached by name): both multi-agent tests share one
        # room. Starts with Agent A + User; Agent B joins during the flow.
        room_id, _user_id, _user_name = await e2e_room_allocator("agno_multi_agent")
        agent_a_id, agent_a_name = e2e_agent_info
        agent_b_id, agent_b_name = e2e_agent_info_2
        run_id = uuid.uuid4().hex[:6]
        turn_timeout = min(float(e2e_config.e2e_timeout) * 3, 100.0)

        log_banner(f"Scenario 2: restart={restart_target} (run {run_id})")

        def build_assistant():
            return build_assistant_adapter(
                e2e_config,
                calculator_id=agent_b_id,
                calculator_name=agent_b_name,
            )

        # --- Turn 1: establish the grocery list with the assistant only ---
        log_step(
            1,
            f"turn 1 — user → {agent_a_name}: grocery list "
            f"[{grocery_list_text()}] (no total yet)",
        )
        async with running_agent(
            build_assistant(),
            agent_id=e2e_config.test_agent_id,
            api_key=e2e_config.band_api_key,
            config=e2e_config,
        ):
            async with listening_for_room_activity(
                ws_client,
                room_id,
                message_types=("text",),
                sender_id=agent_a_id,
                timeout=turn_timeout,
                raise_on_timeout=True,
            ) as wait_a:
                await send_trigger_message(
                    api_client,
                    room_id,
                    (
                        f"(run {run_id}) Here is my grocery list with prices: "
                        f"{grocery_list_text()}. Just confirm you've noted it — "
                        "do NOT total it yet and do NOT bring in anyone else."
                    ),
                    agent_a_name,
                    agent_a_id,
                )
                await wait_a()

        # Turn 1's agent has stopped (context exited). For an A/both restart the
        # fresh instance below must rehydrate the list purely from platform
        # history; for a B restart it's effectively the same first start.
        if restart_target in ("A", "both"):
            log_step("restart", f"{agent_a_name} (assistant) killed → restarting")
            await asyncio.sleep(_RESTART_RECONNECT_DELAY_S)

        # --- Turn 2: ask for the total; assistant brings in the calculator ---
        log_step(
            2,
            f"turn 2 — user → {agent_a_name}: total please; "
            f"{agent_a_name} invites {agent_b_name}",
        )
        async with (
            running_agent(
                build_assistant(),
                agent_id=e2e_config.test_agent_id,
                api_key=e2e_config.band_api_key,
                config=e2e_config,
            ),
            running_agent(
                create_calculator_agno_adapter(e2e_config),
                agent_id=e2e_config.test_agent_id_2,
                api_key=e2e_config.band_api_key_2,
                config=e2e_config,
            ),
        ):
            async with listening_for_room_activity(
                ws_client,
                room_id,
                message_types=("text",),
                sender_id=agent_b_id,
                timeout=turn_timeout,
            ) as wait_b:
                await send_trigger_message(
                    api_client,
                    room_id,
                    (
                        "What's the total of my grocery list? Bring in the "
                        "calculator agent to add up the prices I gave you."
                    ),
                    agent_a_name,
                    agent_a_id,
                )
                await wait_b()

            log_step(3, f"verifying {agent_b_name} ran add_numbers + total via REST")
            await assert_calculator_ran(e2e_session_client_2, room_id)
            await assert_total_reported(e2e_session_client, room_id)

            if restart_target in ("B", "both"):
                # B stays a participant; restart only its process below.
                assert await participant_present(
                    e2e_session_client, room_id, agent_b_id
                ), "Calculator should be a participant before its restart"

        # --- Turn 3 (B / both): restart the calculator, then recompute ---
        if restart_target in ("B", "both"):
            log_step("restart", f"{agent_b_name} (calculator) killed → restarting")
            await asyncio.sleep(_RESTART_RECONNECT_DELAY_S)
            log_step(
                4,
                f"turn 3 — user → {agent_a_name}: ask {agent_b_name} to recompute",
            )
            async with (
                running_agent(
                    build_assistant(),
                    agent_id=e2e_config.test_agent_id,
                    api_key=e2e_config.band_api_key,
                    config=e2e_config,
                ),
                running_agent(
                    create_calculator_agno_adapter(e2e_config),
                    agent_id=e2e_config.test_agent_id_2,
                    api_key=e2e_config.band_api_key_2,
                    config=e2e_config,
                ),
            ):
                async with listening_for_room_activity(
                    ws_client,
                    room_id,
                    message_types=("text",),
                    sender_id=agent_b_id,
                    timeout=turn_timeout,
                ) as wait_b2:
                    await send_trigger_message(
                        api_client,
                        room_id,
                        (
                            "Please ask the calculator agent to add up my "
                            "grocery prices once more and report the total."
                        ),
                        agent_a_name,
                        agent_a_id,
                    )
                    await wait_b2()

                log_step(5, f"verifying restarted {agent_b_name} recomputed via REST")
                # A fresh add_numbers tool_call proves B rehydrated and re-ran.
                await assert_calculator_ran(e2e_session_client_2, room_id)
                await assert_total_reported(e2e_session_client, room_id)

        log_banner(f"Scenario 2 PASSED restart={restart_target} (run {run_id})")
