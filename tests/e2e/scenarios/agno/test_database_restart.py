"""Live smoke: a db-backed Agno agent survives a restart on the full Band stack.

The live-platform counterpart to the in-process round-trip in
``tests/adapters/agno/test_history_persistence.py``. It exercises the full Band
stack with a real Agno agent whose history is owned by a database
(``add_history_to_context=True`` + ``db``):

1. **Talk to it** — a user asks the agent to remember a secret code.
2. **Reboot it** — the agent is stopped and a fresh instance is started against
   the *same* db object and ``session_id`` (a persistent backend outliving the
   process).
3. **It remembers** — the rebooted agent reproduces the code after restart.
4. **History reached Band infra** — the conversation is retrievable from Band's
   REST context.

Scope (deliberately honest): this is a black-box integration test. It cannot
observe the model's assembled context, so it does NOT attempt to prove the
*source* of the recalled history (Agno's db vs. Band) or the absence of
duplication — in the live runtime, prior content can also surface via Band's
"answer the trailing unanswered message" bootstrap path, which the guard does
not govern. The rigorous proof that Band does not rehydrate and the context is
not duplicated lives in the unit test ``test_history_persistence`` (it controls
exactly what Band feeds and asserts it is dropped). Here we additionally assert
the guard is *engaged* in this configuration (``_agno_manages_history``) as a
cheap white-box check.

Run with:
    E2E_TESTS_ENABLED=true uv run pytest \
        tests/e2e/scenarios/agno/test_database_restart.py -v -s --no-cov --log-cli-level=INFO
"""

from __future__ import annotations

import logging
import uuid

import pytest
from agno.db.in_memory import InMemoryDb
from band_rest import AsyncRestClient

from tests.conftest_integration import fetch_all_context
from tests.e2e.conftest import E2ESettings, requires_e2e
from tests.e2e.helpers import (
    TrackingWebSocketClient,
    assert_content_contains,
    listening_for_room_activity,
    log_banner,
    log_step,
    running_agent,
    send_trigger_message,
)
from tests.e2e.scenarios.agno.conftest import build_db_backed_agno_adapter

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
@requires_e2e
class TestAgnoDatabaseRestart:
    """A db-backed Agno agent remembers across a restart on the live Band stack."""

    @pytest.mark.flaky(reruns=2)
    @pytest.mark.timeout(300)
    async def test_db_backed_agent_remembers_after_restart(
        self,
        e2e_config: E2ESettings,
        agno_database_room: tuple[str, str, str],
        e2e_agent_info: tuple[str, str],
        e2e_session_client: AsyncRestClient,
        ws_client: TrackingWebSocketClient,
        api_client: AsyncRestClient,
    ) -> None:
        room_id, _user_id, _user_name = agno_database_room
        agent_id, agent_name = e2e_agent_info
        timeout = min(float(e2e_config.e2e_timeout) * 2, 90.0)
        run_id = uuid.uuid4().hex[:6]
        secret_code = f"SECRET-{run_id}"

        # One db object shared across the "reboot" models a persistent backend;
        # the fixed session_id keys the agent's stored conversation.
        db = InMemoryDb()

        log_banner(f"Scenario: Agno db-backed memory across restart (run {run_id})")

        # --- Phase 1: start the agent and have it store the secret ---
        log_step(1, f"starting db-backed Agno agent (room {room_id})")
        adapter = build_db_backed_agno_adapter(e2e_config, db=db, session_id=room_id)
        # Guard engaged: the adapter has disabled Band's history rehydration.
        assert adapter._agno_manages_history is True

        async with running_agent(
            adapter,
            agent_id=e2e_config.test_agent_id,
            api_key=e2e_config.band_api_key,
            config=e2e_config,
        ):
            log_step(2, f"user asks the agent to remember {secret_code}")
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
                    f"Please remember this secret code for later: {secret_code}. "
                    "Just confirm you will remember it.",
                    agent_name,
                    agent_id,
                )
                await wait_for_reply()

        # --- Phase 2: reboot (fresh instance, same db + session) and recall ---
        log_step("restart", "agent stopped; rebooting with the same db + session_id")
        adapter2 = build_db_backed_agno_adapter(e2e_config, db=db, session_id=room_id)
        assert adapter2._agno_manages_history is True

        async with running_agent(
            adapter2,
            agent_id=e2e_config.test_agent_id,
            api_key=e2e_config.band_api_key,
            config=e2e_config,
        ):
            log_step(3, "user asks the rebooted agent to recall the code")
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
                    "What was the secret code I asked you to remember earlier? "
                    "Reply with just the code.",
                    agent_name,
                    agent_id,
                )
                phase2_responses = await wait_for_reply()

        # The rebooted instance reproduces the code (db-backed memory survived the
        # restart). Source attribution is unit-tested, not claimed here.
        assert_content_contains(phase2_responses, secret_code)
        log_step("assert", "rebooted agent reproduced the code after restart")

        # The conversation persisted to Band infra and is retrievable via REST.
        log_step(4, "verifying the conversation persisted to Band infra via REST")
        items = await fetch_all_context(e2e_session_client, room_id)
        texts = [
            getattr(item, "content", "") or ""
            for item in items
            if getattr(item, "message_type", None) == "text"
        ]
        assert any(secret_code in text for text in texts), (
            f"Expected the secret code {secret_code} in Band's stored room "
            f"context, but it was absent from {len(texts)} text message(s)."
        )
        log_step("assert", "conversation persisted to Band infra (REST context)")

        log_banner(f"Scenario PASSED (run {run_id})")
