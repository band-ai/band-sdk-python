"""Band-side room-shape discovery helpers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from tests.e2e.baseline.toolkit.user_ops import UserOps


class DiscoverableAgent(Protocol):
    """Minimal agent identity for participant-set matching."""

    id: str


async def owner_hub_room_ids(
    *,
    user_ops: UserOps,
    agent: DiscoverableAgent,
    owner_id: str,
    candidate_room_ids: Iterable[str],
) -> set[str]:
    """Return candidate rooms whose active participants are exactly the owner
    and ``agent``.

    The platform does not label a room as a hub; the shared observable shape is
    a room the agent participates in whose participants are exactly the agent
    and its owner.
    """
    expected_participants = {owner_id, agent.id}
    hub_ids: set[str] = set()

    for room_id in candidate_room_ids:
        participant_ids = set(await user_ops.list_participant_ids(room_id))
        if participant_ids == expected_participants:
            hub_ids.add(room_id)

    return hub_ids
