"""Fake AgentTools for unit testing adapters."""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from band.client.rest import (
    AgentContact,
    AgentMemory,
    ListAgentContactRequestsResponse,
    ListAgentContactRequestsResponseData,
    ListAgentContactRequestsResponseMetadata,
    ListAgentContactRequestsResponseMetadataReceived,
    ListAgentContactRequestsResponseMetadataSent,
    ListAgentContactsResponse,
    ListAgentContactsResponseMetadata,
    ListAgentMemoriesResponse,
    ListAgentMemoriesResponseMeta,
    ListAgentPeersResponse,
    ListAgentPeersResponseMetadata,
    Peer,
)
from band.runtime.tools import ToolCallOutcome


def total_pages(total: int, page_size: int) -> int:
    """Page count the platform reports for ``total`` items at ``page_size``."""
    return max(1, (total + page_size - 1) // page_size) if total else 0


def page_slice(
    items: list[dict[str, Any]], page: int, page_size: int
) -> list[dict[str, Any]]:
    """The 1-indexed page of ``items`` the platform would serve."""
    start = (page - 1) * page_size
    return items[start : start + page_size]


class FakeAgentTools:
    """
    Fake implementation of AgentToolsProtocol for testing.

    Tracks all calls and allows assertions on tool usage.
    No mocking framework needed - just use this directly.

    Example:
        async def test_adapter_sends_message():
            adapter = MyAdapter()
            tools = FakeAgentTools()

            await adapter.on_message(msg, tools, history, None,
                                     is_session_bootstrap=True, room_id="room-1")

            assert len(tools.messages_sent) == 1
            assert tools.messages_sent[0]["content"] == "Expected response"
    """

    def __init__(
        self,
        *,
        participants: list[dict[str, Any]] | None = None,
        peers: list[dict[str, Any]] | None = None,
        contacts: list[dict[str, Any]] | None = None,
        room_id: str = "room-fake",
        hub_room_id: str | None = None,
        room_context: list[dict[str, Any]] | None = None,
        memories: list[dict[str, Any]] | None = None,
    ):
        self.room_id = room_id
        self._hub_room_id = hub_room_id
        self.messages_sent: list[dict[str, Any]] = []
        self.events_sent: list[dict[str, Any]] = []
        self._participants: list[dict[str, Any]] = participants or []
        self._room_context: list[dict[str, Any]] = list(room_context or [])
        # Seeds are validated and canonicalized at seed time (not list time),
        # so every stored record carries the real serialized Fern model shape.
        self._peers: list[dict[str, Any]] = [
            Peer.model_validate(peer).model_dump() for peer in (peers or [])
        ]
        self._contacts: list[dict[str, Any]] = [
            AgentContact.model_validate(contact).model_dump()
            for contact in (contacts or [])
        ]
        self.memories: list[dict[str, Any]] = [
            AgentMemory.model_validate(memory).model_dump()
            for memory in (memories or [])
        ]
        self.participants_added: list[dict[str, Any]] = []
        self.participants_removed: list[dict[str, Any]] = []
        self.tool_calls: list[dict[str, Any]] = []
        self.context_calls: list[dict[str, Any]] = []

    @property
    def is_hub_room(self) -> bool:
        """True when this fake is bound to the hub-room execution path.

        Mirrors ``AgentTools.is_hub_room`` so tests that exercise the
        HUB_ROOM auto-enable path (where contact tools are force-exposed)
        can opt in via ``FakeAgentTools(hub_room_id=..., room_id=...)``.
        """
        return self._hub_room_id is not None and self.room_id == self._hub_room_id

    async def send_message(
        self, content: str, mentions: list[str] | list[dict[str, str]] | None = None
    ) -> dict[str, Any]:
        msg = {
            "id": f"msg-{len(self.messages_sent)}",
            "content": content,
            "mentions": mentions or [],
        }
        self.messages_sent.append(msg)
        return msg

    async def send_event(
        self,
        content: str,
        message_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = {
            "id": f"evt-{len(self.events_sent)}",
            "content": content,
            "message_type": message_type,
            "metadata": metadata or {},
        }
        self.events_sent.append(event)
        return event

    async def add_participant(
        self, identifier: str, role: str = "member"
    ) -> dict[str, Any]:
        try:
            participant_id = str(uuid.UUID(identifier))
        except ValueError:
            participant_id = f"p-{identifier}"
        participant = {
            "id": participant_id,
            "name": identifier,
            "role": role,
            "handle": identifier,
        }
        self.participants_added.append(participant)
        if not any(p.get("id") == participant["id"] for p in self._participants):
            self._participants.append(participant)
        return participant

    async def remove_participant(self, identifier: str) -> dict[str, Any]:
        participant = {"id": f"p-{identifier}", "name": identifier}
        self.participants_removed.append(participant)
        return participant

    @property
    def participants(self) -> list[dict[str, Any]]:
        return list(self._participants)

    async def get_participants(self) -> list[dict[str, Any]]:
        return list(self._participants)

    async def lookup_peers(
        self, page: int = 1, page_size: int = 50
    ) -> ListAgentPeersResponse:
        """Return seeded peers in the real SDK's Fern envelope (data/metadata)."""
        return ListAgentPeersResponse(
            data=page_slice(self._peers, page, page_size),
            metadata=ListAgentPeersResponseMetadata(
                page=page,
                page_size=page_size,
                total_count=len(self._peers),
                total_pages=total_pages(len(self._peers), page_size),
            ),
        )

    async def create_chatroom(self, task_id: str | None = None) -> str:
        return f"room-{uuid.uuid4()}"

    def set_room_context(self, messages: list[dict[str, Any]]) -> None:
        """Replace the in-memory room context the fake paginates over."""
        self._room_context = list(messages)

    def append_room_context(self, message: dict[str, Any]) -> None:
        """Append a single message dict to the room context."""
        self._room_context.append(message)

    async def fetch_room_context(
        self,
        *,
        room_id: str,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """Paginate over the configured room_context list."""
        self.context_calls.append(
            {"room_id": room_id, "page": page, "page_size": page_size}
        )
        page_data = page_slice(self._room_context, page, page_size)
        total = len(self._room_context)
        return {
            "data": page_data,
            "meta": {
                "page": page,
                "page_size": page_size,
                "total_count": total,
                "total_pages": total_pages(total, page_size),
            },
        }

    async def list_contacts(
        self, page: int = 1, page_size: int = 50
    ) -> ListAgentContactsResponse:
        """Return seeded contacts in the real SDK's Fern envelope (data/metadata)."""
        return ListAgentContactsResponse(
            data=page_slice(self._contacts, page, page_size),
            metadata=ListAgentContactsResponseMetadata(
                page=page,
                page_size=page_size,
                total_count=len(self._contacts),
                total_pages=total_pages(len(self._contacts), page_size),
            ),
        )

    async def add_contact(
        self, handle: str, message: str | None = None
    ) -> dict[str, Any]:
        return {"id": str(uuid.uuid4()), "status": "pending"}

    async def remove_contact(
        self, handle: str | None = None, contact_id: str | None = None
    ) -> dict[str, Any]:
        return {"status": "removed"}

    async def list_contact_requests(
        self, page: int = 1, page_size: int = 50, sent_status: str = "pending"
    ) -> ListAgentContactRequestsResponse:
        """Return the real SDK's Fern envelope; the fake tracks no request
        state, so both directions list empty."""
        return ListAgentContactRequestsResponse(
            data=ListAgentContactRequestsResponseData(received=[], sent=[]),
            metadata=ListAgentContactRequestsResponseMetadata(
                page=page,
                page_size=page_size,
                received=ListAgentContactRequestsResponseMetadataReceived(
                    total=0, total_pages=0
                ),
                sent=ListAgentContactRequestsResponseMetadataSent(
                    total=0, total_pages=0
                ),
            ),
        )

    async def respond_contact_request(
        self, action: str, handle: str | None = None, request_id: str | None = None
    ) -> dict[str, Any]:
        status_map = {
            "approve": "approved",
            "reject": "rejected",
            "cancel": "cancelled",
        }
        return {
            "id": request_id or str(uuid.uuid4()),
            "status": status_map.get(action, action),
        }

    async def list_memories(
        self,
        subject_id: str | None = None,
        scope: str | None = None,
        system: str | None = None,
        type: str | None = None,
        segment: str | None = None,
        content_query: str | None = None,
        page_size: int = 50,
        status: str | None = None,
    ) -> ListAgentMemoriesResponse:
        """Return stored memories in the real SDK's Fern envelope (data/meta).

        Filters are accepted but not applied; ``page_size`` truncates like the
        real first page. Stored memories are already canonical serialized
        ``AgentMemory`` dicts (validated at store/seed time).
        """
        page = self.memories[:page_size]
        return ListAgentMemoriesResponse(
            data=page,
            meta=ListAgentMemoriesResponseMeta(
                page_size=len(page), total_count=len(self.memories)
            ),
        )

    async def store_memory(
        self,
        content: str,
        system: str,
        type: str,
        segment: str,
        thought: str,
        scope: str,
        subject_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Store and return the memory in the real serialized AgentMemory shape."""
        memory = AgentMemory(
            id=str(uuid.uuid4()),
            content=content,
            system=system,
            type=type,
            segment=segment,
            scope=scope,
            status="active",
            thought=thought,
            subject_id=subject_id,
            metadata=metadata,
            inserted_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        ).model_dump()
        self.memories.append(memory)
        return deepcopy(memory)

    async def get_memory(self, memory_id: str) -> dict[str, Any]:
        """Return a copy of the stored memory; unknown ids raise like the real tool."""
        memory = next((m for m in self.memories if m["id"] == memory_id), None)
        if memory is None:
            raise RuntimeError("Failed to get memory - no response data")
        return deepcopy(memory)

    async def supersede_memory(self, memory_id: str) -> dict[str, Any]:
        return self._set_memory_status(memory_id, "superseded", "supersede")

    async def archive_memory(self, memory_id: str) -> dict[str, Any]:
        return self._set_memory_status(memory_id, "archived", "archive")

    def _set_memory_status(
        self, memory_id: str, status: str, action: str
    ) -> dict[str, Any]:
        for memory in self.memories:
            if memory["id"] == memory_id:
                memory["status"] = status
                return deepcopy(memory)
        raise RuntimeError(f"Failed to {action} memory - no response data")

    @property
    def memory_contents(self) -> list[str]:
        """Contents of the stored memories, oldest first — a readable
        projection for test assertions."""
        return [memory["content"] for memory in self.memories]

    def get_tool_schemas(
        self,
        format: str,
        *,
        include_memory: bool = False,
        include_contacts: bool = True,
    ) -> list[dict[str, Any]]:
        return []

    def get_anthropic_tool_schemas(
        self,
        *,
        include_memory: bool = False,
        include_contacts: bool = True,
    ) -> list[dict[str, Any]]:
        return []

    def get_openai_tool_schemas(
        self,
        *,
        include_memory: bool = False,
        include_contacts: bool = True,
    ) -> list[dict[str, Any]]:
        return []

    async def execute_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        return (await self.execute_tool_call_structured(tool_name, arguments)).value

    async def execute_tool_call_structured(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> ToolCallOutcome:
        """Record the call and report success. Override in a subclass to return
        ``ok=False`` (a base tool failing without raising) for failure-path tests."""
        self.tool_calls.append({"tool_name": tool_name, "arguments": arguments})
        return ToolCallOutcome(value={"status": "ok"}, ok=True)

    # --- Assertion helpers ---

    def assert_message_sent(
        self,
        *,
        content: str | None = None,
        mentions: list[str] | None = None,
        count: int | None = None,
    ) -> None:
        """Assert that a message was sent, optionally matching content/mentions/count."""
        if count is not None:
            assert len(self.messages_sent) == count, (
                f"Expected {count} messages, got {len(self.messages_sent)}"
            )
        if content is not None:
            matching = [m for m in self.messages_sent if m["content"] == content]
            assert matching, (
                f"No message with content {content!r} found. "
                f"Sent: {[m['content'] for m in self.messages_sent]}"
            )
        if mentions is not None:
            matching = [m for m in self.messages_sent if m["mentions"] == mentions]
            assert matching, (
                f"No message with mentions {mentions!r} found. "
                f"Sent: {[m['mentions'] for m in self.messages_sent]}"
            )

    def assert_event_sent(
        self,
        *,
        message_type: str | None = None,
        count: int | None = None,
    ) -> None:
        """Assert that an event was sent; ``count`` counts events of
        ``message_type`` when one is given, otherwise all events."""
        matching = [
            e
            for e in self.events_sent
            if message_type is None or e["message_type"] == message_type
        ]
        if count is None and message_type is None:
            assert matching, "Expected at least one event; none were sent"
        if count is not None:
            assert len(matching) == count, (
                f"Expected {count} {message_type or 'total'} events, "
                f"got {len(matching)}. "
                f"Sent types: {[e['message_type'] for e in self.events_sent]}"
            )
        if message_type is not None:
            assert matching, (
                f"No event with type {message_type!r} found. "
                f"Sent types: {[e['message_type'] for e in self.events_sent]}"
            )

    def assert_no_messages_sent(self) -> None:
        """Assert that no messages were sent."""
        assert not self.messages_sent, (
            f"Expected no messages, but {len(self.messages_sent)} were sent"
        )
