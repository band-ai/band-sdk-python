"""Smoke test for PeerActor — driving a provisioned peer agent (the Echo bounce).

Validates the tool, not any L-level contract: provision two peers in a room, have
one post a message *as itself* mentioning the other, and confirm the message lands
attributed to the sending peer (not the user).
"""

from __future__ import annotations

import pytest


from tests.e2e.baseline.toolkit.provisioning import ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps


@pytest.mark.asyncio(loop_scope="session")
async def test_peer_actor_sends_as_agent(
    resource_manager: ResourceManager, user_ops: UserOps
) -> None:
    sender = await resource_manager.provision_agent("peer-sender")
    target = await resource_manager.provision_agent("peer-target")
    room_id = await resource_manager.provision_room(
        title="e2e-peer-ops", participants=[sender.id, target.id]
    )

    actor = resource_manager.peer(sender)
    message_id = await actor.send_message(
        room_id, "ECHO: hello", mention_id=target.id, mention_name=target.name
    )
    assert message_id, "send_message should return the new message id"

    messages = await user_ops.list_messages(room_id)
    assert any(m.id == message_id and m.sender_id == sender.id for m in messages), (
        "the peer's message should be persisted and attributed to the sending peer"
    )
