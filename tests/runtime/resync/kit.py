"""Shared helpers for reconciliation and resync tests."""

from __future__ import annotations

from datetime import UTC, datetime

from band.runtime.execution import ExecutionContext
from band.runtime.types import PlatformMessage
from tests.runtime.helpers import (
    LifecyclePlatform,
)

ReconciledRoom = tuple[ExecutionContext, LifecyclePlatform, list[str]]


def make_platform_message(
    msg_id: str = "msg-1", room_id: str = "room-1"
) -> PlatformMessage:
    return PlatformMessage(
        id=msg_id,
        room_id=room_id,
        content="Hello",
        sender_id="user-999",
        sender_type="User",
        sender_name="Tester",
        message_type="text",
        metadata={},
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
