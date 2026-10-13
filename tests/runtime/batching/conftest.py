"""Fixtures shared by the batching suites."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.runtime.types import SessionConfig


@pytest.fixture
def ctx() -> MagicMock:
    """An uninitialized room context with two known participants."""
    context = MagicMock()
    context.room_id = "room-1"
    context.is_llm_initialized = False
    context.config = SessionConfig(consume_all_queued=True)
    context.participants = [
        {"id": "alice", "name": "Alice", "type": "User"},
        {"id": "bob", "name": "Bob", "type": "User"},
    ]
    context.peek_pending_system_messages = MagicMock(return_value=[])
    context.get_context = AsyncMock(
        return_value=MagicMock(messages=[], participants=[])
    )
    return context
