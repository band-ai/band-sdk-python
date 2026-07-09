"""Matrix scenario: recall a buried fact and ignore cross-talk in a busy room.

A three-party room (the agent under test, a bystander agent, and the user) is
flooded with chatter addressed to the *bystander*, carrying decoy values. Across
every adapter this checks two properties the quiet room-isolation scenario cannot:

1. Needle-in-haystack recall — a neutral "project id" is seeded, then buried under
   distractor messages with decoy tokens. When asked, the agent must recall the
   seeded value, not a decoy, and must not time out on the busy history.
2. Ignoring cross-talk — the noise is addressed to the bystander, not the agent.
   A liveness probe (an unrelated question addressed to the agent, sent last)
   proves the agent processed past all the noise (per-room FIFO): its answer can
   only arrive after every earlier message. We then assert the agent answered the
   probe and never echoed a decoy — a tolerant sign it didn't engage chatter meant
   for someone else.

Replaces the now-removed legacy ``tests/e2e/scenarios/test_noisy_busy_room.py``.
That test asserted an exact "spoke exactly once" count; the baseline bans exact-count /
mandatory-silence assertions (agents are non-deterministic), so selective silence
is expressed with the tolerant ``assert_contains_none`` over the agent's replies
during the flood instead. The bystander is a provisioned identity that is never
run — a valid, realistic mention target for the cross-talk, needing no LLM key.

Wording note: a neutral "project id", not a "secret code" (models refuse to echo
a credential-shaped value — an unrelated false failure).
"""

from __future__ import annotations

import pytest

from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_agents import liveness_probe, unique_marker
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps


# CREWAI_FLOW is a terminal echo flow with no memory (like codex/opencode), so it
# cannot recall a seeded fact — exclude it from recall scenarios.
@per_adapter(exclude={Adapter.CREWAI_FLOW})
@pytest.mark.flaky(reruns=2, rerun_except=["AssertionError"])  # only transient failures
@pytest.mark.timeout(extra=300)  # seed + several noise inferences + probe + recall
@pytest.mark.asyncio(loop_scope="session")
async def test_recall_and_ignore_crosstalk_in_busy_room(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    baseline_settings: BaselineSettings,
) -> None:
    """Recall a buried fact and stay off chatter addressed to a bystander."""
    needle = unique_marker("project")
    decoys = [unique_marker("weather"), unique_marker("color"), unique_marker("build")]
    live = unique_marker("live")

    # A bystander to address the noise to — provisioned (a valid mention target)
    # but never run, so it needs no provider key and simply never replies.
    bystander = await resource_manager.provision_agent(f"bystander-{agent.adapter_id}")
    room_id = await resource_manager.provision_room(
        title=f"e2e-noisy-{agent.adapter_id}",
        participants=[agent.id, bystander.id],
    )
    # The agent processes the room one message at a time, so the probe answer only
    # arrives after it has chewed through every noise message — budget several
    # sequential inferences off the single-source per-turn timeout.
    flood_deadline = baseline_settings.e2e_timeout * 4

    async with reply_capture(room_id) as capture:
        # Phase 1: seed the needle (addressed to our agent).
        mid = await user_ops.send_message(
            room_id,
            f"Please note for later — the project id is {needle}. Just acknowledge.",
            mention_id=agent.id,
            mention_name=agent.name,
        )
        await capture.wait_for_processed(mid, agent.id)

        # Phase 2: flood with chatter addressed to the BYSTANDER, each carrying a
        # decoy, then a liveness probe addressed to our agent (sent last).
        flood_mark = capture.messages.snapshot()
        for decoy in decoys:
            await user_ops.send_message(
                room_id,
                f"the {decoy} value is noted, thanks.",
                mention_id=bystander.id,
                mention_name=bystander.name,
            )
        probe = await user_ops.send_message(
            room_id,
            liveness_probe(live),
            mention_id=agent.id,
            mention_name=agent.name,
        )
        # Barrier on delivery state, not reply text: per-room FIFO means the probe
        # is PROCESSED only after every earlier (decoy) message was, and the reply
        # is emitted before PROCESSED — so once this returns, any decoy reply the
        # agent wrongly made is already buffered, with no WS-ordering race (the
        # reason the toolkit prefers this over text matching). Only our agent
        # replies (the bystander never runs), so no sender filtering is needed.
        flood_replies = await capture.wait_for_reply(
            probe, agent.id, since=flood_mark, deadline_s=flood_deadline
        )
        # Liveness: it answered its own probe.
        flood_replies.assert_contains_any([live])
        # Ignored cross-talk: nothing it said during the flood echoed a decoy.
        flood_replies.assert_contains_none(decoys)

        # Phase 3: recall the buried needle (addressed to our agent).
        recall_mark = capture.messages.snapshot()
        mid = await user_ops.send_message(
            room_id,
            "What is the project id? Reply with just it.",
            mention_id=agent.id,
            mention_name=agent.name,
        )
        recall = await capture.wait_for_reply(mid, agent.id, since=recall_mark)
        recall.assert_contains_any([needle])
        recall.assert_contains_none(decoys)
