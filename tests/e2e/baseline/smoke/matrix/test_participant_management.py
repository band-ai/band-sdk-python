"""Matrix scenario: the agent invites a peer, directs a message to it, then removes it.

Platform participant-management across every tool-loop adapter: the agent is driven
to (1) add an out-of-room peer via band_add_participant, (2) send that peer one
directed band_send_message carrying a marker, then (3) remove it via
band_remove_participant. Membership is asserted from platform state
(``user_ops.list_participant_ids``) — present after the invite, absent after the
remove — so those checks are model-independent.

The directed message uses the *coupled* filter
(``from_sender(agent).mentioning(peer).assert_contains_any``) so the mention and the
marker must land in the SAME message; a separate ``assert_mentions`` +
``assert_contains_any`` could false-green across two different messages.

This is the matrix version (plus removal) of the recruitment step in
``test_multi_agent_collaboration`` (which is a heterogeneous ``@with_adapters`` cast
with no removal), so it overlaps only slightly.
"""

from __future__ import annotations

import pytest

from tests.e2e.baseline.agents import per_adapter
from tests.e2e.baseline.smoke.samples.sample_agents import (
    invite_and_message_instruction,
    remove_participant_instruction,
    unique_marker,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps


@per_adapter(runs_tool_loop=True)
@pytest.mark.flaky(reruns=2)  # tool invocation + directed message are model-driven
@pytest.mark.timeout(extra=180)  # invite + directed message, then remove (two turns)
@pytest.mark.asyncio(loop_scope="session")
async def test_invites_messages_and_removes_a_peer(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Invite a peer (present), direct a marker message to it, then remove it (absent)."""
    marker = unique_marker("directed")
    echo = await resource_manager.provision_agent("echo")
    room_id = await resource_manager.provision_room(
        title=f"e2e-participant-mgmt-{agent.adapter_id}", participants=[agent.id]
    )

    async with reply_capture(room_id) as capture:
        # Turn 1: invite Echo, then send it one directed message carrying the marker.
        invite_mid = await user_ops.send_message(
            room_id,
            invite_and_message_instruction(echo.name, echo.id, marker),
            mention_id=agent.id,
            mention_name=agent.name,
        )
        replies = await capture.wait_for_reply(invite_mid, agent.id, sender_id=agent.id)

        # State: Echo is a participant after the invite (model-independent).
        after_invite = await user_ops.list_participant_ids(room_id)
        assert echo.id in after_invite, (
            f"expected {echo.name} added to the room; participants: {after_invite}"
        )
        # Coupled: the mention and the marker are in the SAME agent message.
        replies.mentioning(echo.id).assert_contains_any([marker])

        # Turn 2: remove Echo.
        remove_mid = await user_ops.send_message(
            room_id,
            remove_participant_instruction(echo.name, echo.id),
            mention_id=agent.id,
            mention_name=agent.name,
        )
        await capture.wait_for_processed(remove_mid, agent.id)

    after_remove = await user_ops.list_participant_ids(room_id)
    assert echo.id not in after_remove, (
        f"expected {echo.name} removed from the room; participants: {after_remove}"
    )
