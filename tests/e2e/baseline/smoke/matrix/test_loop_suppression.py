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

from band.core.types import AdapterFeatures, Emit, MessageType
from band.runtime.tools import BandTool
from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.flaky import flaky_model
from tests.e2e.baseline.smoke.samples.sample_agents import (
    LIVENESS_REPLY_PROMPT,
    fyi_handoff_instruction,
    liveness_probe,
    unique_marker,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import (
    AdapterCell,
    ProvisionedAgent,
    ResourceManager,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps

# A deliberately high ceiling on the agent's own messages in the post-peer window: a
# normal turn emits one reply (a chatty model a small handful), while an adapter
# looping on its own output emits far more. Not an exact-count assertion — a guard.
LOOP_CEILING = 5


@per_adapter(prompt=LIVENESS_REPLY_PROMPT)
@flaky_model("the peer-driven reply is a model decision")
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
        replies = await capture.wait_for_reply(peer_mid, agent.id)
        # Positive: the peer-authored message drove a real reply from the AGENT (scope
        # to the agent — Echo is itself an Agent, so its own probe is captured too).
        replies.assert_contains_exact(marker)

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


@per_adapter(
    Adapter.CODEX,
    Adapter.COPILOT_ACP,
    Adapter.CURSOR_ACP,
    Adapter.OMP_ACP,
    features=AdapterFeatures(emit={Emit.TOOL_CALLS}),
)
@pytest.mark.timeout(extra=360)
@pytest.mark.asyncio(loop_scope="session")
async def test_two_running_agents_decline_an_fyi_without_a_loop(
    cell: AdapterCell,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    marker = unique_marker("handoff")
    async with cell.run_many(2, labels=["peer-a", "peer-b"]) as (a, b):
        room_id = await resource_manager.provision_room(
            title=f"e2e-fyi-handoff-{cell.adapter_id}",
            participants=[a.id, b.id],
        )
        async with reply_capture(room_id) as capture:
            mark = capture.messages.snapshot()
            mid = await user_ops.send_message(
                room_id,
                fyi_handoff_instruction(b.name, marker),
                mention_id=a.id,
                mention_name=a.name,
            )
            outgoing = await capture.wait_for_reply(mid, a.id, since=mark)
            routed = outgoing.mentioning(b.id)
            routed.assert_contains_exact(marker)
            handoff = next(message for message in routed if marker in message.content)
            boundary = capture.turn_boundary()
            await capture.wait_for_processed(handoff.id, b.id)
            calls = await capture.tool_calls(sender_id=b.id, since=boundary)
            calls.assert_fired(BandTool.NO_REPLY)
            messages = await capture.events(
                MessageType.TEXT, sender_id=b.id, since=boundary
            )
            messages.assert_none()
            messages_a = await capture.events(
                MessageType.TEXT, sender_id=a.id, since=boundary
            )
            messages_a.excluding(handoff.id).assert_none()
            for agent in (a, b):
                errors = await capture.errors(sender_id=agent.id, since=boundary)
                errors.assert_none()
            for agent in (a, b):
                probe = unique_marker("liveness")
                snapshot = capture.messages.snapshot()
                probe_mid = await user_ops.send_message(
                    room_id,
                    liveness_probe(probe),
                    mention_id=agent.id,
                    mention_name=agent.name,
                )
                replies = await capture.wait_for_reply(
                    probe_mid, agent.id, since=snapshot
                )
                replies.assert_contains_exact(probe)
                capture.messages.since(mark).from_sender(agent.id).assert_at_most(
                    LOOP_CEILING
                )
