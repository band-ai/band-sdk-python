from __future__ import annotations

from datetime import UTC, datetime

from band.adapters.agno import AgnoAdapter
from band.core.types import PlatformMessage
from tests.adapters.agno.helpers import (
    CapturingModel,
)

BAND_SENTINEL = "BAND-REHYDRATED-SENTINEL"

ROOM_ID = "room-roundtrip"


def _platform_message(msg_id: str, content: str) -> PlatformMessage:
    return PlatformMessage(
        id=msg_id,
        room_id=ROOM_ID,
        content=content,
        sender_id="user-1",
        sender_type="User",
        sender_name="Alice",
        message_type="text",
        metadata={},
        created_at=datetime.now(UTC),
    )


def _captured(adapter: AgnoAdapter) -> CapturingModel:
    agent = adapter.agent
    assert agent is not None
    model = agent.model
    assert isinstance(model, CapturingModel)
    return model
