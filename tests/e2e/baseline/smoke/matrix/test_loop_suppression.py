"""Matrix scenario: a peer message drives one turn, with no self-triggered send loop.

Two properties from one peer-driven flow, across the full matrix. Echo (a
provisioned, non-running peer, added to the room up front so a ``PeerActor`` can post
as it) sends ONE directed liveness probe mentioning the agent:

* Positive (subsumes the retired anthropic-only ``test_peer_actor``): the agent's own
  reply carries the probe marker — a peer-authored message reached the agent's
  inference exactly like a user's and drove a real turn.
* Loop-suppression: after the peer turn settles, a follow-up user probe is sent and
  barriered; per-room FIFO orders the peer turn and any self-dispatch it spawned ahead
  of the probe's reply, so a runaway would already be captured. The agent's own
  messages since the snapshot must stay at/below a deliberately high ceiling — a normal
  one-turn reply batch never crosses it, but an adapter re-dispatching on its own
  output does. An infinite loop starves the probe and fails via the barrier timeout.

The upper-bound check is the one sanctioned ``assert_at_most`` — a runaway guard, not
an exact-reply count: it proves the adapter doesn't re-process its own output without
making model-driven reply batching part of the contract.
"""

from __future__ import annotations

import pytest

from tests.e2e.baseline.agents import per_adapter
from tests.e2e.baseline.smoke.samples.sample_agents import (
    REPLY_PROMPT,
    liveness_probe,
    unique_marker,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps

# A deliberately high ceiling on the agent's own messages in the post-peer window: a
# normal turn emits one reply (a chatty model a small handful), while an adapter
# looping on its own output emits far more. Not an exact-count assertion — a guard.
LOOP_CEILING = 5


@per_adapter(prompt=REPLY_PROMPT)
@pytest.mark.flaky(reruns=2)
@pytest.mark.timeout(extra=180)  # a peer turn, then a follow-up probe turn
@pytest.mark.asyncio(loop_scope="session")
async def test_peer_message_drives_turn_without_loop(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """A peer's directed message drives one reply, and the agent does not loop on itself."""
    marker = unique_marker("peer")
    echo = await resource_manager.provision_agent("echo")
    # Echo must already be a participant — a PeerActor can only post to a room it is in.
    room_id = await resource_manager.provision_room(
        title=f"e2e-loop-suppression-{agent.adapter_id}",
        participants=[agent.id, echo.id],
    )

    async with reply_capture(room_id) as capture:
        # Echo posts ONE directed probe (directed, not passive, so it reliably elicits
        # a reply the positive can assert).
        peer_mid = await resource_manager.peer(echo).send_message(
            room_id,
            liveness_probe(marker),
            mention_id=agent.id,
            mention_name=agent.name,
        )
        await capture.wait_for_processed(peer_mid, agent.id)
        # Positive: the peer-authored message drove a real reply from the AGENT (scope
        # to the agent — Echo is itself an Agent, so its own probe is captured too).
        capture.messages.from_sender(agent.id).assert_contains_any([marker])

        # Loop-suppression: snapshot after the peer turn, then a follow-up user probe.
        mark = capture.messages.snapshot()
        probe_mid = await user_ops.send_message(
            room_id,
            liveness_probe(unique_marker("probe")),
            mention_id=agent.id,
            mention_name=agent.name,
        )
        await capture.wait_for_processed(probe_mid, agent.id)
        # FIFO puts any self-dispatch loop ahead of the probe reply, so it's captured
        # by now; the agent's own messages since the snapshot stay under the ceiling.
        capture.messages.since(mark).from_sender(agent.id).assert_at_most(LOOP_CEILING)
