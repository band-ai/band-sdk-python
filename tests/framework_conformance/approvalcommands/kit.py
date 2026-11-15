"""Agent room messages for reading approval dialects."""

from __future__ import annotations

from band.client.streaming import MessageCreatedPayload


def agent_message(content: str) -> MessageCreatedPayload:
    return MessageCreatedPayload(
        id=content,
        content=content,
        message_type="text",
        sender_id="agent",
        sender_type="Agent",
        inserted_at="2026-09-27T00:00:00Z",
        updated_at="2026-09-27T00:00:00Z",
    )
