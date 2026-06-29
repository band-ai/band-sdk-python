"""Smoke test exercising the provisioning (provision/reap) tool.

Validates the tool, not any L-level contract: provision an agent and a room, assert
their state via platform reads, then reap and assert the agent is gone.
"""

from __future__ import annotations

import pytest


from tests.e2e.baseline.toolkit.provisioning import NAME_PREFIX, ResourceManager


@pytest.mark.timeout(120)
@pytest.mark.asyncio(loop_scope="session")
async def test_provision_room_with_agent_then_reap(
    resource_manager: ResourceManager,
) -> None:
    provisioned = await resource_manager.provision_agent("smoke")
    assert provisioned.id and provisioned.api_key
    assert provisioned.name.startswith(NAME_PREFIX)

    room_id = await resource_manager.provision_room(
        title="e2e-provisioning-smoke", participants=[provisioned.id]
    )

    # State via platform read: the provisioned agent is a participant of the room.
    participant_ids = await resource_manager.user_ops.list_participant_ids(room_id)
    assert provisioned.id in participant_ids, (
        "provisioned agent should be a room participant"
    )

    # Reap and confirm the agent no longer appears in the user's agent list.
    await resource_manager.reap_all()
    response = await resource_manager.client.human_api_agents.list_my_agents(
        name=NAME_PREFIX, page_size=100
    )
    remaining_ids = [agent.id for agent in response.data]
    assert provisioned.id not in remaining_ids, "agent should be gone after reap"
