"""Smoke test exercising the user-operations tool.

Validates the tool, not any L-level contract: create a room, read its
participants, then delete it (the REST-backed delete path).
"""

from __future__ import annotations

import pytest


from tests.e2e.baseline.toolkit.user_ops import UserOps


@pytest.mark.timeout(120)
@pytest.mark.asyncio(loop_scope="session")
async def test_user_ops_create_list_delete_room(user_ops: UserOps) -> None:
    room_id = await user_ops.create_room(title="e2e-userops-smoke")
    try:
        participant_ids = await user_ops.list_participant_ids(room_id)
        assert isinstance(participant_ids, list), "participant_ids should be a list"
    finally:
        await user_ops.delete_room(room_id)
