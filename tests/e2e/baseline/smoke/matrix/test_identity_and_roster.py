"""Matrix scenarios: room roster reads — tool-driven and passive-injection.

Two complementary probes:

* ``test_reports_identity_and_roster`` — the agent must use platform tools
  (``band_get_participants`` / ``band_lookup_peers``) to report who is in the
  room and who is invitable. Every expected value is *self-sourced* so assertions
  can't drift (agent name, in-room peer name, out-of-room name from its lookup
  result). Concurrent runs may add other valid invitable peers.
* ``test_reports_peer_description_from_passive_roster`` — the agent must answer
  from the always-injected participants list alone (no roster tools), including
  each peer's ``description``. Guards the passive roster's description
  surfacing end to end (REST load -> participant cache -> roster message).

Three *separate* tolerant assertions over one scoped reply collection in the
tool-driven smoke (an any-of over all three would green on just one). The room
UUID and the user's display name stay out under the floors-only policy.
"""

from __future__ import annotations

import asyncio
import re

import pytest

from tests.e2e.baseline.agents import per_adapter
from tests.e2e.baseline.flaky import flaky_model
from tests.e2e.baseline.smoke.samples.sample_agents import (
    PASSIVE_ROSTER_DESCRIPTIONS_PROBE,
    ROSTER_LOOKUP_PAGE_SIZE,
    ROSTER_PROBE,
    TOOL_AGENT,
    unique_marker,
)
from tests.e2e.baseline.smoke.samples.sample_tools import EXECUTION_REPORTING
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.observations.tool_calls import RosterTool
from tests.e2e.baseline.toolkit.observations.tool_results import ToolResults
from tests.e2e.baseline.toolkit.provisioning import (
    NAME_PREFIX,
    ProvisionedAgent,
    ResourceManager,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps


def lookup_peer_names(results: ToolResults) -> set[str]:
    """Test-agent names present in actual lookup output, independent of encoding."""
    name_pattern = re.compile(
        rf"[\"']name[\"']\s*:\s*[\"']({re.escape(NAME_PREFIX)}[0-9a-f]+-[a-zA-Z0-9_-]+)[\"']"
    )
    return {name for result in results for name in name_pattern.findall(result.output)}


@per_adapter(runs_tool_loop=True, **TOOL_AGENT, **EXECUTION_REPORTING)
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
    member, invitable = await asyncio.gather(
        resource_manager.provision_agent("member"),
        resource_manager.provision_agent("invitable"),
    )
    room_id = await resource_manager.provision_room(
        title=f"e2e-identity-roster-{agent.adapter_id}",
        participants=[agent.id, member.id],
    )

    # Precondition: the room has at least one invitable peer; other live runs can
    # add more, so any name in this roster is a valid lookup-backed answer.
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
        try:
            replies = await capture.wait_for_reply(mid, agent.id, since=mark)
        except TimeoutError as exc:
            calls, results = await asyncio.gather(
                capture.tool_calls(sender_id=agent.id),
                capture.tool_results(sender_id=agent.id),
            )
            raise TimeoutError(
                f"{exc}; tool calls: {[call.name for call in calls]}; "
                f"tool results: {[(result.name, result.is_error) for result in results]}"
            ) from exc
        await capture.wait_for_processed(mid, agent.id)
        calls = await capture.tool_calls(sender_id=agent.id)
        lookup_results = (await capture.tool_results(sender_id=agent.id)).named(
            RosterTool.LOOKUP_PEERS
        )

    calls.assert_fired(RosterTool.GET_PARTICIPANTS)
    calls.assert_fired(
        RosterTool.LOOKUP_PEERS, with_args={"page_size": ROSTER_LOOKUP_PAGE_SIZE}
    )
    lookup_results.assert_succeeded(RosterTool.LOOKUP_PEERS)
    offered_names = lookup_peer_names(lookup_results) - {agent.name, member.name}
    assert offered_names, "agent lookup returned no out-of-room test-agent names"

    # Each self-sourced value asserted separately over the same replies — an any-of
    # over all three would pass on just one.
    replies.assert_contains_any([agent.name])  # identity (only the SDK knows it)
    replies.assert_contains_any([member.name])  # roster (via band_get_participants)
    replies.assert_contains_any(offered_names)  # invitable (via band_lookup_peers)


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
    ``band_lookup_peers`` (the roster tools whose responses carry description —
    the cheat path) is only possible if the always-injected participants list
    carried those descriptions.
    """
    role, decoy = await asyncio.gather(
        resource_manager.provision_agent(
            "role",
            description=f"Handles exclusively {unique_marker('descrole')} inquiries.",
        ),
        resource_manager.provision_agent(
            "decoy",
            description=f"Handles exclusively {unique_marker('descdecoy')} inquiries.",
        ),
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
    assert not calls.fired(RosterTool.GET_PARTICIPANTS), (
        "band_get_participants fired — description must come from the passive roster, "
        f"not a tool; calls={[c.name for c in calls]}"
    )
    assert not calls.fired(RosterTool.LOOKUP_PEERS), (
        "band_lookup_peers fired — description must come from the passive roster, "
        f"not a tool; calls={[c.name for c in calls]}"
    )
    # Separate asserts: an any-of over both descriptions would green on just one.
    replies.assert_contains_any([role.description])
    replies.assert_contains_any([decoy.description])
