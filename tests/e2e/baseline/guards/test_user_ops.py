"""Smoke test exercising the user-operations tool.

Validates the tool, not any L-level contract: create a room, read its
participants, then delete it (the REST-backed delete path).
"""

from __future__ import annotations

import pytest
from band_rest import Peer


from tests.e2e.baseline.toolkit.provisioning import ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps

_PEER_PAGE_SIZE = 100


async def _all_invitable_agents(user_ops: UserOps, room_id: str) -> list[Peer]:
    """Every Agent peer invitable to ``room_id``, paged so the check never assumes
    the target lands on page 1 in a workspace that has accumulated agents."""
    collected: list[Peer] = []
    page = 1
    while True:
        batch = await user_ops.lookup_peers(
            not_in_room=room_id, peer_type="Agent", page=page, limit=_PEER_PAGE_SIZE
        )
        collected.extend(batch)
        if len(batch) < _PEER_PAGE_SIZE:
            return collected
        page += 1


@pytest.mark.asyncio(loop_scope="session")
async def test_user_ops_create_list_delete_room(user_ops: UserOps) -> None:
    room_id = await user_ops.create_room(title="e2e-userops-smoke")
    try:
        participant_ids = await user_ops.list_participant_ids(room_id)
        assert isinstance(participant_ids, list), "participant_ids should be a list"
    finally:
        await user_ops.delete_room(room_id)


@pytest.mark.asyncio(loop_scope="session")
async def test_user_ops_lookup_peers_excludes_room_members(
    user_ops: UserOps, resource_manager: ResourceManager
) -> None:
    """A known owned peer is invitable to a room it's absent from, gone once added.

    Provisions an agent (owned by the test user, so it appears in the user's
    peer list) but never runs it — no LLM, no provider key. Asserting a *known*
    id keeps every check non-vacuous: it proves the ``Agent`` type filter is
    honored and, above all, the ``not_in_room`` exclusion semantics that make
    this the "who could I invite here" query. Room + agent are reaped by the
    manager on teardown.
    """
    peer = await resource_manager.provision_agent(label="lookup-peer")
    room_id = await resource_manager.provision_room(title="e2e-userops-peers")

    invitable = await _all_invitable_agents(user_ops, room_id)
    match = next((p for p in invitable if p.id == peer.id), None)
    assert match is not None, (
        "a provisioned agent should be invitable to a room it isn't in"
    )
    assert isinstance(match, Peer) and match.name == peer.name, (
        "the matched peer should carry the fields callers match on"
    )
    assert all(p.type == "Agent" for p in invitable), (
        "the Agent type filter was not honored"
    )

    await user_ops.add_participant(room_id, peer.id)
    after = await _all_invitable_agents(user_ops, room_id)
    assert all(p.id != peer.id for p in after), (
        "a peer already in the room must drop out of the invitable set"
    )
