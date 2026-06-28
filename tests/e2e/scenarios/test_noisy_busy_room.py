"""E2E test for an agent in a noisy, busy, multi-party room.

The room-isolation scenario deliberately uses *fresh* rooms so accumulated
history can't bloat rehydration into timeouts. This scenario covers the
opposite case: a room that is genuinely noisy — three participants and a burst
of chatter, most of it addressed to *someone else* — and verifies the agent
still behaves correctly.

Two properties are checked together, for every adapter:

1. Needle-in-haystack recall — a target fact ("project id") is seeded, then
   buried under distractor chatter carrying decoy values. When asked, the agent
   must recall the seeded fact, not a decoy, and must not time out on the busy
   history.
2. Selective silence — the distractor chatter is addressed to other
   participants. The preprocessor delivers every room message to the agent
   (it only filters the agent's own messages), so the agent runs an inference
   per message but must stay silent on chatter not directed at it.

The silence check uses a *liveness probe* rather than waiting for "no answer"
(which can't tell silent-on-purpose from slow/dead): after the noise we ask the
agent an unrelated direct question. Because a room's messages are processed in
order, the probe answer arriving proves the agent already worked past every
noise message — so if it had replied to any, that reply would have arrived
first. Collecting every reply from the flood through the probe answer makes the
*count* meaningful: exactly one (the probe answer) means it stayed silent.

Run with:
    E2E_TESTS_ENABLED=true uv run pytest tests/e2e/scenarios/test_noisy_busy_room.py -v -s --no-cov
"""

from __future__ import annotations

import logging
import uuid

import pytest
from band_rest import AsyncRestClient
from band_rest.types import ParticipantRequest

from band.agent import Agent

from tests.e2e.adapters.conftest import AdapterFactory
from tests.e2e.settings import E2ESettings, RoomAllocator, requires_e2e
from tests.e2e.helpers import (
    TrackingWebSocketClient,
    assert_content_contains,
    assert_no_content_contains,
    listening_for_agent_responses,
    listening_for_room_activity,
    log_banner,
    log_step,
    send_agent_message,
    send_trigger_message,
)

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
@requires_e2e
class TestNoisyBusyRoom:
    """An agent must recall the right fact and stay silent on cross-talk."""

    @pytest.mark.flaky(reruns=2)
    @pytest.mark.timeout(300)
    async def test_recall_and_silence_in_noisy_room(
        self,
        e2e_config: E2ESettings,
        ws_client: TrackingWebSocketClient,
        adapter_entry: tuple[str, AdapterFactory],
        api_client: AsyncRestClient,
        e2e_fresh_room_allocator: RoomAllocator,
        e2e_agent_info: tuple[str, str],
        e2e_session_client_2: AsyncRestClient,
        e2e_agent_info_2: tuple[str, str],
    ):
        """Recall a buried fact and ignore chatter addressed to others.

        Wording note: the seeded fact is a neutral "project id", not a "secret
        code" — models refuse to repeat back a credential-shaped value, a false
        failure unrelated to what this test checks.
        """
        adapter_name, factory = adapter_entry
        agent_id, agent_name = e2e_agent_info
        agent_2_id, agent_2_name = e2e_agent_info_2
        timeout = e2e_config.e2e_timeout
        # The agent processes the room's messages one at a time, so the probe
        # answer only arrives after it has chewed through every noise message.
        # Give that window room for several sequential inferences.
        flood_timeout = timeout * 3

        # Per-run tokens so cross-run history can't make an assertion pass (or
        # fail) by coincidence; adapter-prefixed to keep the transcript clear.
        # Decoy stems are distinct whole words (not single letters) so none can
        # be a substring of the needle — e.g. the needle ends in "...ANTHROPIC_
        # <id>", which a "C_<id>" decoy would falsely match.
        run_id = uuid.uuid4().hex[:6]
        needle = f"PROJECT_{adapter_name.upper()}_{run_id}"
        weather = f"WEATHER_{run_id}"
        color = f"COLOR_{run_id}"
        build = f"BUILD_{run_id}"
        decoys = (weather, color, build)
        live = f"LIVE_{run_id}"

        log_banner(f"[{adapter_name}] Noisy busy room — recall + selective silence")

        # --- Phase 1: multi-party room (agent + user + agent_2) ---
        room_id, user_id, user_name = await e2e_fresh_room_allocator("noisy-room")
        await api_client.human_api_participants.add_my_chat_participant(
            chat_id=room_id,
            participant=ParticipantRequest(participant_id=agent_2_id, role="member"),
        )
        parts = await api_client.human_api_participants.list_my_chat_participants(
            room_id
        )
        part_ids = {p.id for p in (parts.data or [])}
        assert {agent_id, user_id, agent_2_id} <= part_ids, (
            f"[{adapter_name}] expected a multi-party room with agent, user and "
            f"agent_2; participants were {part_ids}"
        )
        log_step(
            1,
            f"room={room_id} participants=[agent={agent_name}, user={user_name}, "
            f"agent_2={agent_2_name}]",
        )

        adapter = factory(e2e_config)
        agent = Agent.create(
            adapter=adapter,
            agent_id=e2e_config.test_agent_id,
            api_key=e2e_config.band_api_key,
            ws_url=e2e_config.band_ws_url,
            rest_url=e2e_config.band_base_url,
        )

        async with agent:
            # --- Phase 2: seed the needle (addressed to our agent) ---
            async with listening_for_agent_responses(
                ws_client, room_id, timeout=timeout, raise_on_timeout=True
            ) as wait:
                await send_trigger_message(
                    api_client,
                    room_id,
                    f"Please note for later — the project id is {needle}. "
                    "Just acknowledge.",
                    agent_name,
                    agent_id,
                )
                ack = await wait()
            assert len(ack) >= 1, (
                f"[{adapter_name}] agent never acknowledged the seeded fact"
            )
            log_step(2, f"seeded needle={needle}; agent acked ({len(ack)} msg)")

            # --- Phase 3: flood with noise addressed to OTHERS, then probe ---
            # min_messages is set above any plausible count so the window ends
            # only when the probe answer (sentinel `live`) arrives — letting us
            # count every reply the agent made meanwhile.
            async with listening_for_room_activity(
                ws_client,
                room_id,
                timeout=flood_timeout,
                message_types=("text",),
                sender_id=agent_id,
                min_messages=99,
                stop_substring=live,
                raise_on_timeout=True,
            ) as wait:
                await send_trigger_message(
                    api_client,
                    room_id,
                    f"FYI the weather token is {weather}.",
                    agent_2_name,
                    agent_2_id,
                )
                await send_agent_message(
                    e2e_session_client_2,
                    room_id,
                    f"Thanks. For the record, the color code is {color}.",
                    user_name,
                    user_id,
                )
                await send_trigger_message(
                    api_client,
                    room_id,
                    f"Got it. Also the build number is {build}.",
                    agent_2_name,
                    agent_2_id,
                )
                await send_agent_message(
                    e2e_session_client_2,
                    room_id,
                    "Acknowledged, nothing further.",
                    user_name,
                    user_id,
                )
                # Unrelated direct question — the liveness probe.
                await send_trigger_message(
                    api_client,
                    room_id,
                    f"Reply with just the word {live} and nothing else.",
                    agent_name,
                    agent_id,
                )
                during_noise = await wait()

            contents = [m.content for m in during_noise]
            log_step(
                3,
                f"posted 4 noise msgs (decoys {weather}/{color}/{build}); probe={live}",
            )
            # Liveness: the probe was answered, so the agent is alive and has
            # processed past all the noise.
            assert_content_contains(during_noise, live)
            # Selective silence: the probe answer is the ONLY thing it said. Any
            # reply to the addressed-to-others noise would be an extra entry.
            assert len(during_noise) == 1, (
                f"[{adapter_name}] agent should have spoken exactly once (the "
                f"probe answer) but said {len(during_noise)}: {contents} — it "
                "replied to chatter addressed to other participants"
            )
            log_step("assert", f"silent on cross-talk; replies={contents}")

            # --- Phase 4: recall the buried needle (addressed to our agent) ---
            async with listening_for_agent_responses(
                ws_client, room_id, timeout=timeout, raise_on_timeout=True
            ) as wait:
                await send_trigger_message(
                    api_client,
                    room_id,
                    "What is the project id? Reply with just it.",
                    agent_name,
                    agent_id,
                )
                recall = await wait()
            assert len(recall) >= 1, (
                f"[{adapter_name}] agent never answered the recall question"
            )
            assert_content_contains(recall, needle)
            for decoy in decoys:
                assert_no_content_contains(recall, decoy)
            log_step(
                4,
                f"recall reply={[m.content for m in recall]}; "
                f"found {needle}, no decoys",
            )

        log_banner(
            f"[{adapter_name}] PASSED: busy room — correct recall + selective silence"
        )
