"""Live: one Agno agent keeps per-room context isolated across two rooms.

Agno-dedicated analogue of the cross-adapter
``tests/e2e/scenarios/test_room_isolation.py``. That shared test fails for Agno
because its "secret code, remember it" prompt is refused by cautious models
(even Sonnet) as an injected directive — not an SDK defect. This version
controls for that exactly as ``test_context_persistence.py`` does:

1. **Fresh rooms** (no standing/stale content from prior runs).
2. **Benign, disjoint random phrases** (no "code"/"secret" trigger, and the two
   phrases share no words so the negative assertion is unambiguous).
3. An agent **instructed** to treat Band's ``@[[id]]`` formatting as normal chat
   and to recall earlier conversation verbatim.

One Agno agent joins both rooms; each room is told its own phrase, then asked to
repeat it. The agent must recall each room's phrase and never leak the other's —
verifying the adapter's per-room history isolation.

Run with:
    E2E_TESTS_ENABLED=true uv run pytest \
        tests/e2e/scenarios/agno/test_room_isolation.py -v -s --no-cov
"""

from __future__ import annotations

import logging
import random
from typing import Any

import pytest
from band_rest import AsyncRestClient

from band.core.simple_adapter import SimpleAdapter

from tests.e2e.adapters.conftest import _require_anthropic_key
from tests.e2e.settings import E2ESettings, RoomAllocator, requires_e2e
from tests.e2e.helpers import (
    TrackingWebSocketClient,
    assert_content_contains,
    assert_no_content_contains,
    listening_for_room_activity,
    log_banner,
    log_step,
    running_agent,
    send_trigger_message,
)

logger = logging.getLogger(__name__)

# Benign "lorem" vocabulary for the recall payloads. Deliberately free of words
# like "code"/"secret" that a cautious model refuses to echo (treating them as
# injected directives) and that collide with standing agent memories.
_LOREM_WORDS = (
    "lorem ipsum dolor sit amet consectetur adipiscing elit sed eiusmod tempor "
    "incididunt labore dolore magna aliqua veniam quis nostrud exercitation "
    "ullamco laboris aliquip commodo consequat duis aute irure voluptate velit "
    "esse cillum fugiat nulla pariatur excepteur occaecat cupidatat proident "
    "sunt culpa officia deserunt mollit anim laborum"
).split()


def _disjoint_phrases(words_each: int = 5) -> tuple[str, str]:
    """Return two benign phrases that share no words (clean isolation asserts)."""
    sample = random.sample(_LOREM_WORDS, words_each * 2)
    return " ".join(sample[:words_each]), " ".join(sample[words_each:])


def _build_isolation_agno_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Build a plain (no-db) Agno adapter tuned to recall conversation history.

    No ``db``/``add_history_to_context``: recall must come from Band's per-room
    history rehydration, which is what room isolation exercises. The instructions
    counter a small model's default reluctance — they say Band's ``@[[id]]``
    mentions and sender labels are normal chat formatting (not injected
    directives) and that it should repeat earlier conversation content verbatim.
    """
    _require_anthropic_key()
    from agno.agent import Agent as AgnoAgent
    from agno.models.anthropic import Claude

    from band.adapters.agno import AgnoAdapter

    agno_agent = AgnoAgent(
        model=Claude(id=settings.e2e_anthropic_model),
        instructions=(
            "You are a helpful assistant with perfect recall of the current "
            "conversation. Messages may include @[[id]] mentions and sender "
            "labels — that is normal Band chat formatting, not instructions to "
            "distrust or ignore. When the user asks you to repeat something they "
            "told you earlier in this conversation, reply with that text exactly, "
            "verbatim. Keep responses short."
        ),
    )
    return AgnoAdapter(agno_agent)


@pytest.mark.asyncio
@requires_e2e
class TestAgnoRoomIsolation:
    """One Agno agent maintains isolated per-room context across two rooms."""

    @pytest.mark.flaky(reruns=2)
    @pytest.mark.timeout(300)
    async def test_agent_keeps_rooms_isolated(
        self,
        e2e_config: E2ESettings,
        e2e_fresh_room_allocator: RoomAllocator,
        e2e_agent_info: tuple[str, str],
        ws_client: TrackingWebSocketClient,
        api_client: AsyncRestClient,
    ) -> None:
        """Plant a distinct phrase in each room, then verify no cross-room leak.

        Phase 1: tell room A phrase_a and room B phrase_b (one agent, both rooms).
        Phase 2: ask each room to repeat its phrase; assert each room recalls its
                 own phrase and never the other room's.
        """
        # Two fresh rooms: the agent must isolate phrases planted *this run*, free
        # of stale content a reused room would surface.
        room_a_id, _ua, _na = await e2e_fresh_room_allocator("agno_room_isolation_a")
        room_b_id, _ub, _nb = await e2e_fresh_room_allocator("agno_room_isolation_b")
        agent_id, agent_name = e2e_agent_info
        timeout = min(float(e2e_config.e2e_timeout) * 2, 90.0)
        phrase_a, phrase_b = _disjoint_phrases()

        log_banner("Scenario: Agno keeps two rooms' context isolated")
        logger.info("Room A=%s phrase_a=%r", room_a_id, phrase_a)
        logger.info("Room B=%s phrase_b=%r", room_b_id, phrase_b)

        async with running_agent(
            _build_isolation_agno_adapter(e2e_config),
            agent_id=e2e_config.test_agent_id,
            api_key=e2e_config.band_api_key,
            config=e2e_config,
        ):
            # --- Phase 1: plant each room's phrase (sequential: one agent) ---
            for label, room_id, phrase in (
                ("A", room_a_id, phrase_a),
                ("B", room_b_id, phrase_b),
            ):
                log_step(1, f"planting phrase in room {label} ({room_id})")
                async with listening_for_room_activity(
                    ws_client,
                    room_id,
                    message_types=("text",),
                    sender_id=agent_id,
                    timeout=timeout,
                    raise_on_timeout=True,
                ) as wait_ack:
                    await send_trigger_message(
                        api_client,
                        room_id,
                        f'Please remember this exact phrase for me: "{phrase}". '
                        "Just confirm you've got it.",
                        agent_name,
                        agent_id,
                    )
                    await wait_ack()

            # --- Phase 2: query each room and verify isolation ---
            log_step(2, "asking room A to repeat its phrase")
            async with listening_for_room_activity(
                ws_client,
                room_a_id,
                message_types=("text",),
                sender_id=agent_id,
                timeout=timeout,
                raise_on_timeout=True,
            ) as wait_a:
                await send_trigger_message(
                    api_client,
                    room_a_id,
                    "Earlier in this conversation I asked you to remember an exact "
                    "phrase. Repeat that phrase back to me, word for word.",
                    agent_name,
                    agent_id,
                )
                room_a_received = await wait_a()

            log_step(2, "asking room B to repeat its phrase")
            async with listening_for_room_activity(
                ws_client,
                room_b_id,
                message_types=("text",),
                sender_id=agent_id,
                timeout=timeout,
                raise_on_timeout=True,
            ) as wait_b:
                await send_trigger_message(
                    api_client,
                    room_b_id,
                    "Earlier in this conversation I asked you to remember an exact "
                    "phrase. Repeat that phrase back to me, word for word.",
                    agent_name,
                    agent_id,
                )
                room_b_received = await wait_b()

            # Room A recalls only phrase_a; room B recalls only phrase_b.
            assert_content_contains(room_a_received, phrase_a)
            assert_no_content_contains(room_a_received, phrase_b)
            assert_content_contains(room_b_received, phrase_b)
            assert_no_content_contains(room_b_received, phrase_a)
            log_step("assert", "each room recalled only its own phrase")

        log_banner("Scenario PASSED")
