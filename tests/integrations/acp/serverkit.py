"""Shared ACP server message and prompt observations."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from band.core.types import MessageType, PlatformMessage
from band.integrations.acp.server_adapter import BandACPServerAdapter
from band.integrations.acp.types import ACPSessionState, PendingACPPrompt
from band.testing import FakeAgentTools


def make_platform_message(
    content: str,
    room_id: str = "room-123",
    message_type: str = MessageType.TEXT,
    sender_id: str = "peer-456",
    sender_name: str = "Test Peer",
) -> PlatformMessage:
    """Create a test PlatformMessage."""
    return PlatformMessage(
        id=str(uuid4()),
        room_id=room_id,
        content=content,
        sender_id=sender_id,
        sender_type="Agent",
        sender_name=sender_name,
        message_type=message_type,
        metadata={},
        created_at=datetime.now(UTC),
    )


def failure_event(
    metadata: object = None, content: str = "Peer failed", *, room_id: str = "room-123"
) -> PlatformMessage:
    """An error event with optional room failure metadata."""
    return replace(
        make_platform_message(content, room_id=room_id, message_type=MessageType.ERROR),
        metadata={} if metadata is None else metadata,
    )


async def deliver_server_message(
    adapter: BandACPServerAdapter, msg: PlatformMessage
) -> None:
    """Deliver one room message through the ACP server adapter."""
    await adapter.on_message(
        msg,
        FakeAgentTools(),
        ACPSessionState(),
        None,
        None,
        is_session_bootstrap=False,
        room_id=msg.room_id,
    )


async def wait_for_pending_prompt(
    adapter: BandACPServerAdapter, room_id: str
) -> PendingACPPrompt:
    """Wait until a pending prompt has been posted to a room.

    ``handle_prompt`` blocks until the peer replies, so tests dispatch it as
    a task and use this to wait for the prompt to reach the room. Registration
    alone is earlier: room events before the post belong to the previous turn.
    """
    while True:
        pending = adapter._pending_prompts.get(room_id)
        if pending is not None and pending.posted:
            return pending
        await asyncio.sleep(0)
