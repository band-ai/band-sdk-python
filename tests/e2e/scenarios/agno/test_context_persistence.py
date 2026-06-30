"""Live: an Agno agent recalls prior conversation from Band history on rejoin.

The shared cross-adapter recall test
(``tests/e2e/scenarios/test_context_persistence.py``) excludes Agno because two
of its behaviors break that test's assumptions without being SDK defects:

1. On the shared, reused room a "secret code" prompt collides with standing
   organization-scoped agent memories phrased as a "code name", so the agent
   recalls the wrong value.
2. The small default agent distrusts Band's ``@[[id]]``-formatted history,
   refusing to act on it ("each request stands on its own").

This test covers the same capability for Agno specifically while controlling for
both: a **fresh room** (no standing/stale content), a **benign random phrase**
(no "code" collision), an agent **instructed** to treat Band chat formatting as
normal and recall verbatim, and the rate-limit-aware ``running_agent`` for the
rapid restart.

Unlike ``test_database_restart.py``, this agent has **no Agno db**: recall must
come from Band's history rehydration (``is_session_bootstrap``) on rejoin — that
is the behavior under test.

Run with:
    E2E_TESTS_ENABLED=true uv run pytest \
        tests/e2e/scenarios/agno/test_context_persistence.py -v -s --no-cov
"""

from __future__ import annotations

import logging
import random
from typing import Any

import pytest
from band_rest import AsyncRestClient

from band.core.simple_adapter import SimpleAdapter

from tests.conftest_integration import fetch_all_context
from tests.e2e.adapters.conftest import _require_anthropic_key
from tests.e2e.settings import E2ESettings, RoomAllocator, requires_e2e
from tests.e2e.helpers import (
    TrackingWebSocketClient,
    assert_content_contains,
    listening_for_room_activity,
    log_banner,
    log_step,
    running_agent,
    send_trigger_message,
)

logger = logging.getLogger(__name__)

# Benign "lorem" vocabulary for the recall payload. Deliberately free of words
# like "code"/"secret" that a cautious small model refuses to echo (treating
# them as injected directives) and that collide with standing agent memories
# phrased as a "code name".
_LOREM_WORDS = (
    "lorem ipsum dolor sit amet consectetur adipiscing elit sed eiusmod tempor "
    "incididunt labore dolore magna aliqua veniam quis nostrud exercitation "
    "ullamco laboris aliquip commodo consequat duis aute irure voluptate velit "
    "esse cillum fugiat nulla pariatur excepteur occaecat cupidatat proident "
    "sunt culpa officia deserunt mollit anim laborum"
).split()


def _recall_phrase() -> str:
    """Return a random, benign five-word phrase for the recall assertion."""
    return " ".join(random.sample(_LOREM_WORDS, 5))


def _build_recall_agno_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Build a plain (no-db) Agno adapter tuned to recall conversation history.

    No ``db``/``add_history_to_context``: recall must come from Band's history
    rehydration. The instructions counter the small model's default reluctance —
    they tell it that Band's ``@[[id]]`` mentions and sender labels are normal
    chat formatting (not injected directives) and that it should repeat earlier
    conversation content verbatim when asked.
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
class TestAgnoContextPersistence:
    """An Agno agent recalls prior context from Band history after a restart."""

    @pytest.mark.flaky(reruns=2)
    @pytest.mark.timeout(300)
    async def test_agent_recalls_phrase_after_restart(
        self,
        e2e_config: E2ESettings,
        e2e_fresh_room_allocator: RoomAllocator,
        e2e_agent_info: tuple[str, str],
        e2e_session_client: AsyncRestClient,
        ws_client: TrackingWebSocketClient,
        api_client: AsyncRestClient,
    ) -> None:
        """Plant a phrase, restart the agent, and assert it recalls the phrase.

        Phase 1: ask the agent to remember a benign random phrase; wait for ack.
        Phase 2: stop it, start a fresh instance (Band rehydrates history via
                 ``is_session_bootstrap``), ask it to repeat the phrase verbatim.
        Then assert the phrase reached Band's stored room context (REST).
        """
        # Fresh room: the agent must recall the phrase planted *this run*, not
        # stale content or standing memories a reused room would surface.
        room_id, _user_id, _user_name = await e2e_fresh_room_allocator(
            "agno_context_persistence"
        )
        agent_id, agent_name = e2e_agent_info
        timeout = min(float(e2e_config.e2e_timeout), 90.0)
        phrase = _recall_phrase()

        log_banner("Scenario: Agno recalls Band-rehydrated history after restart")
        logger.info("Recall phrase for this run: %r", phrase)

        # --- Phase 1: plant the phrase ---
        log_step(1, f"starting Agno agent and planting a phrase (room {room_id})")
        async with running_agent(
            _build_recall_agno_adapter(e2e_config),
            agent_id=e2e_config.test_agent_id,
            api_key=e2e_config.band_api_key,
            config=e2e_config,
        ):
            async with listening_for_room_activity(
                ws_client,
                room_id,
                message_types=("text",),
                sender_id=agent_id,
                timeout=timeout,
                raise_on_timeout=True,
            ) as wait_for_ack:
                await send_trigger_message(
                    api_client,
                    room_id,
                    f'Please remember this exact phrase for me: "{phrase}". '
                    "Just confirm you've got it.",
                    agent_name,
                    agent_id,
                )
                await wait_for_ack()

        # --- Phase 2: restart (fresh instance, no db) and recall via Band history ---
        log_step("restart", "agent stopped; starting a fresh instance to recall")
        async with running_agent(
            _build_recall_agno_adapter(e2e_config),
            agent_id=e2e_config.test_agent_id,
            api_key=e2e_config.band_api_key,
            config=e2e_config,
        ):
            log_step(2, "asking the rebooted agent to repeat the phrase")
            async with listening_for_room_activity(
                ws_client,
                room_id,
                message_types=("text",),
                sender_id=agent_id,
                timeout=timeout,
                raise_on_timeout=True,
            ) as wait_for_recall:
                await send_trigger_message(
                    api_client,
                    room_id,
                    "Earlier in this conversation I asked you to remember an exact "
                    "phrase. Repeat that phrase back to me, word for word.",
                    agent_name,
                    agent_id,
                )
                phase2_responses = await wait_for_recall()

            assert_content_contains(phase2_responses, phrase)
            log_step("assert", "rebooted agent recalled the phrase from Band history")

        # The conversation persisted to Band infra and is retrievable via REST.
        log_step(3, "verifying the phrase persisted to Band infra via REST")
        items = await fetch_all_context(e2e_session_client, room_id)
        texts = [
            getattr(item, "content", "") or ""
            for item in items
            if getattr(item, "message_type", None) == "text"
        ]
        assert any(phrase in text for text in texts), (
            f"Expected the phrase {phrase!r} in Band's stored room context, but "
            f"it was absent from {len(texts)} text message(s)."
        )
        log_step("assert", "phrase persisted to Band infra (REST context)")

        log_banner("Scenario PASSED")
