"""Shared SimpleAdapter test stubs used across core tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from band.core.protocols import HistoryConverter
from band.core.simple_adapter import SimpleAdapter
from band.core.types import AgentInput, HistoryProvider, MessageType, PlatformMessage
from band.testing import FakeAgentTools


class RecordingAdapter(SimpleAdapter[str]):
    """Records ``on_message`` / ``on_cleanup`` calls for verification."""

    def __init__(self, *, history_converter: HistoryConverter[str] | None = None):
        super().__init__(history_converter=history_converter)
        self.calls: list[dict[str, Any]] = []
        self.cleanup_calls: list[str] = []

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: Any,
        history: Any,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        self.calls.append(
            {
                "msg": msg,
                "tools": tools,
                "history": history,
                "participants_msg": participants_msg,
                "contacts_msg": contacts_msg,
                "is_session_bootstrap": is_session_bootstrap,
                "room_id": room_id,
            }
        )

    async def on_cleanup(self, room_id: str) -> None:
        self.cleanup_calls.append(room_id)


def make_platform_message(
    content: str = "Hello",
    *,
    room_id: str = "room-1",
    message_id: str = "msg-1",
    sender_id: str = "user-1",
    sender_name: str = "Alice",
) -> PlatformMessage:
    return PlatformMessage(
        id=message_id,
        room_id=room_id,
        content=content,
        sender_id=sender_id,
        sender_type="User",
        sender_name=sender_name,
        message_type=MessageType.TEXT,
        metadata={},
        created_at=datetime.now(UTC),
    )


def make_agent_input(
    content: str = "Hello",
    *,
    msg: PlatformMessage | None = None,
    tools: Any | None = None,
    raw_history: list[dict[str, Any]] | None = None,
    participants_msg: str | None = None,
    contacts_msg: str | None = None,
    is_session_bootstrap: bool = False,
    room_id: str | None = None,
) -> AgentInput:
    """Build an ``AgentInput``; pass ``msg=`` when the test already has one."""
    resolved_room = room_id or (tools.room_id if tools is not None else "room-1")
    resolved_tools = tools or FakeAgentTools(room_id=resolved_room)
    return AgentInput(
        msg=msg or make_platform_message(content, room_id=resolved_room),
        tools=resolved_tools,
        history=HistoryProvider(raw=raw_history or []),
        participants_msg=participants_msg,
        contacts_msg=contacts_msg,
        is_session_bootstrap=is_session_bootstrap,
        room_id=resolved_room,
    )
