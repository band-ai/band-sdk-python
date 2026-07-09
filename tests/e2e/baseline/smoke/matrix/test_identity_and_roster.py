"""Matrix scenario: an agent reports its identity and reads the room roster.

The platform-adaptation probe, across every tool-loop adapter: the agent is asked
to state its own name and — using its platform tools (band_get_participants /
band_lookup_peers) — to report who is in the room and who it could invite. Every
expected value is *self-sourced* so the assertions can't drift:

* the agent's own name (identity), which only the running SDK knows;
* a known-named peer we provisioned into the room (roster), returned by the agent's
  band_get_participants;
* a known-named peer we provisioned but left OUT of the room (invitable). The agent
  has no other way to learn that name — the out-of-room peer is never mentioned — so
  the name appearing in the reply is itself proof the agent read the invitable roster
  via band_lookup_peers. ``user_ops.lookup_peers`` confirms the peer really is
  invitable first, so a broken setup fails as a precondition, not as the agent's turn.

Three *separate* tolerant assertions over the one scoped reply collection (an any-of
over all three would green on just one). The room UUID and the user's display name
are deliberately not asserted: small models paraphrase a raw UUID away, and no
toolkit read returns a participant's display name — both would be flaky or
un-sourceable, so they stay out under the floors-only policy.
"""

from __future__ import annotations

import pytest

from tests.e2e.baseline.agents import per_adapter
from tests.e2e.baseline.smoke.samples.sample_agents import ROSTER_PROBE
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps


@per_adapter(runs_tool_loop=True)
@pytest.mark.flaky(reruns=2)  # small-model wording of names is non-deterministic
@pytest.mark.timeout(extra=120)  # a turn with two platform-tool reads
@pytest.mark.asyncio(loop_scope="session")
async def test_reports_identity_and_roster(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """The agent names itself, a room member, and an invitable out-of-room peer."""
    member = await resource_manager.provision_agent("member")
    invitable = await resource_manager.provision_agent("invitable")
    room_id = await resource_manager.provision_room(
        title=f"e2e-identity-roster-{agent.adapter_id}",
        participants=[agent.id, member.id],
    )

    # Precondition: the out-of-room peer really is invitable from this room, so the
    # agent's own band_lookup_peers can surface it (its Peer.name is what we assert).
    roster = await user_ops.lookup_peers(not_in_room=room_id)
    assert invitable.id in {peer.id for peer in roster}, (
        f"expected {invitable.name} to be invitable to the room; "
        f"roster ids: {[peer.id for peer in roster]}"
    )

    async with reply_capture(room_id) as capture:
        mark = capture.messages.snapshot()
        mid = await user_ops.send_message(
            room_id, ROSTER_PROBE, mention_id=agent.id, mention_name=agent.name
        )
        replies = await capture.wait_for_reply(
            mid, agent.id, sender_id=agent.id, since=mark
        )

    # Each self-sourced value asserted separately over the same replies — an any-of
    # over all three would pass on just one.
    replies.assert_contains_any([agent.name])  # identity (only the SDK knows it)
    replies.assert_contains_any([member.name])  # roster (via band_get_participants)
    replies.assert_contains_any([invitable.name])  # invitable (via band_lookup_peers)
