"""Canonical platform fixtures for baseline conformance tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from band.client.streaming import (
    MessageCreatedPayload,
    MessageMetadata,
    Mention,
    ParticipantAddedPayload,
    ParticipantRemovedPayload,
)
from band.core.types import AgentInput
from band.platform.event import (
    MessageEvent,
    ParticipantAddedEvent,
    ParticipantRemovedEvent,
)
from band.preprocessing.default import DefaultPreprocessor

ROOM_ID = "11111111-1111-4111-8111-111111111111"
USER_ID = "22222222-2222-4222-8222-222222222222"
AGENT_ID = "33333333-3333-4333-8333-333333333333"
PEER_AGENT_ID = "44444444-4444-4444-8444-444444444444"
SECOND_PEER_AGENT_ID = "55555555-5555-4555-8555-555555555555"
CURRENT_MESSAGE_ID = "msg-current-trigger"
AGENT_HANDLE = "darvell/test-agent"
USER_HANDLE = "darvell"
PEER_AGENT_HANDLE = "darvell/calc"
SECOND_PEER_AGENT_HANDLE = "darvell/greeter"

_EPOCH = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)


@dataclass
class ConformanceExecutionContext:
    room_id: str = ROOM_ID
    agent_id: str = AGENT_ID
    history_messages: list[dict[str, Any]] = field(default_factory=list)
    pending_system_messages: list[str] = field(default_factory=list)
    enable_context_hydration: bool = True
    is_llm_initialized: bool = False
    participants: list[dict[str, Any]] = field(default_factory=list)
    link: SimpleNamespace = field(
        default_factory=lambda: SimpleNamespace(rest=SimpleNamespace())
    )
    processed_message_ids: set[str] = field(default_factory=set)
    pending_work_ids: list[str] = field(default_factory=list)
    completed_tool_call_ids: set[str] = field(default_factory=set)
    _last_participants_sent: list[dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        if not self.participants:
            self.participants = canonical_participants()
        self.config = SimpleNamespace(
            enable_context_hydration=self.enable_context_hydration
        )

    async def get_context(self) -> SimpleNamespace:
        return SimpleNamespace(
            messages=list(self.history_messages), participants=self.participants
        )

    def mark_llm_initialized(self) -> None:
        self.is_llm_initialized = True

    def participants_changed(self) -> bool:
        if self._last_participants_sent is None:
            return True
        last_ids = {p.get("id") for p in self._last_participants_sent}
        current_ids = {p.get("id") for p in self.participants}
        return last_ids != current_ids

    def mark_participants_sent(self) -> None:
        self._last_participants_sent = [
            dict(participant) for participant in self.participants
        ]

    def get_pending_system_messages(self) -> list[str]:
        messages = list(self.pending_system_messages)
        self.pending_system_messages.clear()
        return messages


def iso_timestamp(offset_seconds: int) -> str:
    return (
        (_EPOCH + timedelta(seconds=offset_seconds)).isoformat().replace("+00:00", "Z")
    )


def canonical_participants() -> list[dict[str, Any]]:
    return [
        {
            "id": USER_ID,
            "name": "Darvell",
            "type": "User",
            "handle": USER_HANDLE,
        },
        {
            "id": AGENT_ID,
            "name": "Test Agent",
            "type": "Agent",
            "handle": AGENT_HANDLE,
        },
        {
            "id": PEER_AGENT_ID,
            "name": "Calc",
            "type": "Agent",
            "handle": PEER_AGENT_HANDLE,
        },
    ]


def canonical_peers() -> list[dict[str, Any]]:
    return [
        {
            "id": SECOND_PEER_AGENT_ID,
            "name": "Greeter",
            "type": "Agent",
            "handle": SECOND_PEER_AGENT_HANDLE,
        }
    ]


def history_message(
    *,
    message_id: str,
    content: str,
    sender_id: str,
    sender_type: str,
    sender_name: str,
    offset_seconds: int,
    message_type: str = "text",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    inserted_at = iso_timestamp(offset_seconds)
    return {
        "id": message_id,
        "chat_room_id": ROOM_ID,
        "content": content,
        "sender_id": sender_id,
        "sender_type": sender_type,
        "sender_name": sender_name,
        "message_type": message_type,
        "metadata": metadata or {"mentions": [], "source_message_id": message_id},
        "inserted_at": inserted_at,
        "updated_at": inserted_at,
    }


def canonical_history() -> list[dict[str, Any]]:
    return [
        history_message(
            message_id="msg-history-001",
            content="User planted MARCO in the conversation.",
            sender_id=USER_ID,
            sender_type="User",
            sender_name="Darvell",
            offset_seconds=1,
        ),
        history_message(
            message_id="msg-history-002",
            content="Test Agent acknowledged LIGHTHOUSE as the project.",
            sender_id=AGENT_ID,
            sender_type="Agent",
            sender_name="Test Agent",
            offset_seconds=2,
        ),
        history_message(
            message_id="msg-history-003",
            content="Calc mentioned POSTGRESQL as the database.",
            sender_id=PEER_AGENT_ID,
            sender_type="Agent",
            sender_name="Calc",
            offset_seconds=3,
        ),
    ]


def completed_tool_history() -> list[dict[str, Any]]:
    return [
        history_message(
            message_id="msg-tool-call-001",
            content=(
                '{"name":"band_send_message","args":{"content":"done",'
                '"mentions":["@darvell"]},"tool_call_id":"tool-call-001"}'
            ),
            sender_id=AGENT_ID,
            sender_type="Agent",
            sender_name="Test Agent",
            offset_seconds=4,
            message_type="tool_call",
        ),
        history_message(
            message_id="msg-tool-result-001",
            content=(
                '{"name":"band_send_message","output":{"id":"msg-sent"},'
                '"tool_call_id":"tool-call-001"}'
            ),
            sender_id=AGENT_ID,
            sender_type="Agent",
            sender_name="Test Agent",
            offset_seconds=5,
            message_type="tool_result",
        ),
    ]


def current_message_payload(
    *,
    content: str = "@darvell/test-agent please recall the room facts.",
    message_id: str = CURRENT_MESSAGE_ID,
) -> MessageCreatedPayload:
    timestamp = iso_timestamp(6)
    return MessageCreatedPayload(
        id=message_id,
        content=content,
        message_type="text",
        metadata=MessageMetadata(
            mentions=[Mention(id=AGENT_ID, handle=AGENT_HANDLE, name="Test Agent")],
            status="sent",
        ),
        sender_id=USER_ID,
        sender_type="User",
        sender_name="Darvell",
        chat_room_id=ROOM_ID,
        thread_id=None,
        inserted_at=timestamp,
        updated_at=timestamp,
    )


def current_message_event(
    *,
    content: str = "@darvell/test-agent please recall the room facts.",
    message_id: str = CURRENT_MESSAGE_ID,
) -> MessageEvent:
    return MessageEvent(
        room_id=ROOM_ID,
        payload=current_message_payload(content=content, message_id=message_id),
        raw={"event": "message_created", "room_id": ROOM_ID},
    )


def pending_work_state() -> dict[str, Any]:
    return {
        "pending_message_id": "msg-offline-pending",
        "processed_message_ids": {"msg-history-002"},
        "completed_tool_call_ids": {"tool-call-001"},
    }


def participant_added_event(
    *,
    participant_id: str = SECOND_PEER_AGENT_ID,
    name: str = "Greeter",
    participant_type: str = "Agent",
    handle: str = SECOND_PEER_AGENT_HANDLE,
) -> ParticipantAddedEvent:
    return ParticipantAddedEvent(
        room_id=ROOM_ID,
        payload=ParticipantAddedPayload(
            id=participant_id,
            name=name,
            type=participant_type,
            handle=handle,
        ),
        raw={"event": "participant_added", "room_id": ROOM_ID},
    )


def participant_removed_event(
    *,
    participant_id: str = SECOND_PEER_AGENT_ID,
) -> ParticipantRemovedEvent:
    return ParticipantRemovedEvent(
        room_id=ROOM_ID,
        payload=ParticipantRemovedPayload(id=participant_id),
        raw={"event": "participant_removed", "room_id": ROOM_ID},
    )


def apply_participant_event(
    ctx: ConformanceExecutionContext,
    event: ParticipantAddedEvent | ParticipantRemovedEvent,
) -> None:
    if isinstance(event, ParticipantAddedEvent):
        if event.payload is None:
            return
        participant = event.payload.model_dump()
        if any(
            existing.get("id") == participant.get("id") for existing in ctx.participants
        ):
            return
        ctx.participants.append(
            {
                "id": participant.get("id"),
                "name": participant.get("name"),
                "type": participant.get("type"),
                "handle": participant.get("handle"),
            }
        )
        return

    if event.payload is None:
        return
    ctx.participants = [
        participant
        for participant in ctx.participants
        if participant.get("id") != event.payload.id
    ]


async def build_agent_input_through_preprocessor(
    *,
    ctx: ConformanceExecutionContext | None = None,
    event: MessageEvent | None = None,
    agent_id: str = AGENT_ID,
) -> AgentInput:
    preprocessor = DefaultPreprocessor()
    agent_input = await preprocessor.process(
        ctx or ConformanceExecutionContext(history_messages=canonical_history()),
        event or current_message_event(),
        agent_id=agent_id,
    )
    if agent_input is None:
        raise AssertionError("canonical message event should produce AgentInput")
    return agent_input
