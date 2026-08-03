"""Matrix scenarios: room roster reads — tool-driven and passive-injection.

Two complementary probes:

* ``test_reports_identity_and_roster`` — the agent must use platform tools
  (``band_get_participants`` / ``band_lookup_peers``) to report who is in the
  room and who is invitable. Every expected value is *self-sourced* so assertions
  can't drift (agent name, in-room peer name, out-of-room invitable name).
* ``test_reports_peer_description_from_passive_roster`` — the agent must answer
  from the always-injected participants list alone (no roster tools), including
  each peer's ``description``. Red until the passive roster surfaces descriptions
  that today are dropped at load/tracker.

Three *separate* tolerant assertions over one scoped reply collection in the
tool-driven smoke (an any-of over all three would green on just one). The room
UUID and the user's display name stay out under the floors-only policy.
"""

from __future__ import annotations

import pytest
from tests.e2e.baseline.flaky import flaky_model

from tests.e2e.baseline.agents import per_adapter
from tests.e2e.baseline.smoke.samples.sample_agents import (
    PASSIVE_ROSTER_DESCRIPTIONS_PROBE,
    ROSTER_PROBE,
    unique_marker,
)
from tests.e2e.baseline.smoke.samples.sample_tools import EXECUTION_REPORTING
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps

# Roster tools that return ChatParticipantDetails / Peer with description — the
# cheat path this passive-roster smoke must not take (see CLAUDE.md chat tools).
GET_PARTICIPANTS_TOOL = "band_get_participants"
LOOKUP_PEERS_TOOL = "band_lookup_peers"


@per_adapter(runs_tool_loop=True)
@flaky_model("small-model wording of names is non-deterministic")
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
        replies = await capture.wait_for_reply(mid, agent.id, since=mark)

    # Each self-sourced value asserted separately over the same replies — an any-of
    # over all three would pass on just one.
    replies.assert_contains_any([agent.name])  # identity (only the SDK knows it)
    replies.assert_contains_any([member.name])  # roster (via band_get_participants)
    replies.assert_contains_any([invitable.name])  # invitable (via band_lookup_peers)


@per_adapter(runs_tool_loop=True, **EXECUTION_REPORTING)
@flaky_model(
    "small-model wording of descriptions / whether to skip tools is non-deterministic"
)
@pytest.mark.timeout(extra=120)
@pytest.mark.asyncio(loop_scope="session")
async def test_reports_peer_description_from_passive_roster(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Peer descriptions are visible in the passive roster without roster tools.

    Two in-room peers get distinct high-entropy descriptions that never appear in
    the user prompt. Quoting both without ``band_get_participants`` /
    ``band_lookup_peers`` is only possible if the always-injected participants
    list carried those descriptions — the field the SDK currently drops.
    """
    role = await resource_manager.provision_agent(
        "role",
        description=f"Handles exclusively {unique_marker('descrole')} inquiries.",
    )
    decoy = await resource_manager.provision_agent(
        "decoy",
        description=f"Handles exclusively {unique_marker('descdecoy')} inquiries.",
    )
    room_id = await resource_manager.provision_room(
        title=f"e2e-passive-roster-desc-{agent.adapter_id}",
        participants=[agent.id, role.id, decoy.id],
    )

    async with reply_capture(room_id) as capture:
        mark = capture.messages.snapshot()
        mid = await user_ops.send_message(
            room_id,
            PASSIVE_ROSTER_DESCRIPTIONS_PROBE,
            mention_id=agent.id,
            mention_name=agent.name,
        )
        replies = await capture.wait_for_reply(mid, agent.id, since=mark)
        # Fresh single-turn room — unscoped is correct. turn_boundary() is a
        # *next*-turn since; using it here would exclude this turn's tool calls.
        calls = await capture.tool_calls(sender_id=agent.id)

    # Tools first: if the model cheated via roster tools, fail for that reason
    # before the description floor (otherwise a missing description masks it).
    assert not calls.fired(GET_PARTICIPANTS_TOOL), (
        "band_get_participants fired — description must come from the passive roster, "
        f"not a tool; calls={[c.name for c in calls]}"
    )
    assert not calls.fired(LOOKUP_PEERS_TOOL), (
        "band_lookup_peers fired — description must come from the passive roster, "
        f"not a tool; calls={[c.name for c in calls]}"
    )
    # Separate asserts: an any-of over both descriptions would green on just one.
    replies.assert_contains_any([role.description])
    replies.assert_contains_any([decoy.description])
