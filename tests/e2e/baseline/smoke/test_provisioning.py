"""Smoke test exercising the provisioning (mint/reap) tool.

Validates the tool, not any L-level contract: mint an agent and a room, assert
their state via platform reads, then reap and assert the agent is gone.
"""

from __future__ import annotations

import pytest

from tests.e2e.baseline.toolkit.provisioning import NAME_PREFIX, ResourceManager


@pytest.mark.asyncio(loop_scope="session")
async def test_mint_room_with_agent_then_reap(
    resource_manager: ResourceManager,
) -> None:
    minted = await resource_manager.mint_agent("smoke")
    assert minted.id and minted.api_key
    assert minted.name.startswith(NAME_PREFIX)

    room_id = await resource_manager.mint_room(
        title="e2e-provisioning-smoke", participants=[minted.id]
    )

    # State via platform read: the minted agent is a participant of the room.
    participant_ids = await resource_manager.user_ops.list_participant_ids(room_id)
    assert minted.id in participant_ids, "minted agent should be a room participant"

    # Reap and confirm the agent no longer appears in the user's agent list.
    await resource_manager.reap_all()
    response = await resource_manager.client.human_api_agents.list_my_agents(
        name=NAME_PREFIX, page_size=100
    )
    remaining_ids = [agent.id for agent in response.data]
    assert minted.id not in remaining_ids, "agent should be gone after reap"
