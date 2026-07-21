"""Fake AgentTools for unit testing adapters."""

from __future__ import annotations

import uuid
from typing import Any

from band.client.rest import ListAgentMemoriesResponse, ListAgentMemoriesResponseMeta
from band.runtime.tools import ToolCallOutcome


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
        self._peers: list[dict[str, Any]] = peers or []
        self._contacts: list[dict[str, Any]] = contacts or []
        self._room_context: list[dict[str, Any]] = list(room_context or [])
        self.memories: list[dict[str, Any]] = list(memories or [])
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

    async def lookup_peers(self, page: int = 1, page_size: int = 50) -> dict[str, Any]:
        return {
            "peers": list(self._peers),
            "metadata": {
                "page": page,
                "page_size": page_size,
                "total": len(self._peers),
            },
        }

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
        start = (page - 1) * page_size
        end = start + page_size
        page_data = self._room_context[start:end]
        total = len(self._room_context)
        total_pages = max(1, (total + page_size - 1) // page_size) if total else 0
        return {
            "data": page_data,
            "meta": {
                "page": page,
                "page_size": page_size,
                "total_count": total,
                "total_pages": total_pages,
            },
        }

    async def list_contacts(self, page: int = 1, page_size: int = 50) -> dict[str, Any]:
        return {
            "contacts": list(self._contacts),
            "metadata": {
                "page": page,
                "page_size": page_size,
                "total_count": len(self._contacts),
                "total_pages": max(
                    1, (len(self._contacts) + page_size - 1) // page_size
                ),
            },
        }

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
    ) -> dict[str, Any]:
        return {
            "received": [],
            "sent": [],
            "metadata": {
                "page": page,
                "page_size": page_size,
                "received": {"total": 0, "total_pages": 0},
                "sent": {"total": 0, "total_pages": 0},
            },
        }

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
        real first page. Memory dicts are coerced into ``AgentMemory`` models,
        so they must carry the platform-required fields (``store_memory``'s
        return shape qualifies).
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
        memory = {
            "id": str(uuid.uuid4()),
            "content": content,
            "system": system,
            "type": type,
            "segment": segment,
            "scope": scope,
            "status": "active",
            "thought": thought,
            "inserted_at": "2025-01-01T00:00:00Z",
        }
        self.memories.append(memory)
        return memory

    async def get_memory(self, memory_id: str) -> dict[str, Any]:
        return {
            "id": memory_id,
            "content": "Test memory content",
            "system": "long_term",
            "type": "semantic",
            "segment": "user",
            "scope": "subject",
            "status": "active",
            "thought": "Test thought",
            "subject_id": None,
            "source_agent_id": None,
            "inserted_at": "2025-01-01T00:00:00Z",
        }

    async def supersede_memory(self, memory_id: str) -> dict[str, Any]:
        return {"id": memory_id, "status": "superseded"}

    async def archive_memory(self, memory_id: str) -> dict[str, Any]:
        return {"id": memory_id, "status": "archived"}

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
        """Assert that an event was sent, optionally matching type/count."""
        if count is not None:
            assert len(self.events_sent) == count, (
                f"Expected {count} events, got {len(self.events_sent)}"
            )
        if message_type is not None:
            matching = [
                e for e in self.events_sent if e["message_type"] == message_type
            ]
            assert matching, (
                f"No event with type {message_type!r} found. "
                f"Sent types: {[e['message_type'] for e in self.events_sent]}"
            )

    def assert_no_messages_sent(self) -> None:
        """Assert that no messages were sent."""
        assert not self.messages_sent, (
            f"Expected no messages, but {len(self.messages_sent)} were sent"
        )
