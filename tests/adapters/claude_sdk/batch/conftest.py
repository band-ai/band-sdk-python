"""A realistic room roster for the batch senders and their controller."""

from __future__ import annotations

from typing import Any

import pytest

from tests.adapters.claude_sdk.batch.kit import ADMIN, ALICE, CAROL, HELPER, OpenRoom
from tests.adapters.claude_sdk.helpers import APPROVER, ClaudeRoom


@pytest.fixture
def claude_room(claude_room: OpenRoom) -> OpenRoom:
    async def open_room(*args: Any, **kwargs: Any) -> ClaudeRoom:
        kwargs.setdefault(
            "participants",
            [
                {**person, "type": "User", "role": "member", "status": "active"}
                for person in (ALICE, HELPER, CAROL, ADMIN, APPROVER)
            ],
        )
        return await claude_room(*args, **kwargs)

    return open_room
