"""Offline delivery scenarios that sit immediately outside the adapter boundary."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.runtime.execution import ExecutionContext
from tests.conftest import make_message_event


def delivery_link() -> MagicMock:
    """Build the minimal local transport contract used by delivery scenarios."""
    link = MagicMock()
    link.agent_id = "agent-baseline"
    link.mark_processing = AsyncMock(return_value=True)
    link.mark_processed = AsyncMock(return_value=True)
    link.mark_failed = AsyncMock(return_value=True)
    return link


@pytest.mark.asyncio
async def test_replayed_message_is_not_delivered_to_an_adapter_twice() -> None:
    """A processed replay must not duplicate an adapter's side effects."""
    link = delivery_link()
    handler = AsyncMock()
    context = ExecutionContext("room-baseline", link, handler, agent_id=link.agent_id)
    context._ensure_fresh_context = AsyncMock()  # type: ignore[method-assign]
    event = make_message_event(room_id="room-baseline", msg_id="message-replayed")

    await context._process_event(event)
    await context._process_event(event)

    handler.assert_awaited_once_with(context, event)
    link.mark_processed.assert_awaited_once_with("room-baseline", "message-replayed")
