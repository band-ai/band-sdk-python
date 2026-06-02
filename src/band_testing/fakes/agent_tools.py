"""Fake AgentTools for unit testing adapters."""

from __future__ import annotations

import uuid
from typing import Any


class FakeAgentTools:
    """Fake implementation of AgentToolsProtocol for testing.

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

    Attributes:
        messages_sent: List of messages sent via send_message()
        events_sent: List of events sent via send_event()
        participants_added: List of participants added via add_participant()
        participants_removed: List of participants removed via remove_participant()
        tool_calls: List of tool calls made via execute_tool_call()
    """

    def __init__(self) -> None:
        self.messages_sent: list[dict[str, Any]] = []
        self.events_sent: list[dict[str, Any]] = []
        self.participants_added: list[dict[str, Any]] = []
        self.participants_removed: list[dict[str, Any]] = []
        self.tool_calls: list[dict[str, Any]] = []

    async def send_message(
        self, content: str, mentions: list[str] | list[dict[str, str]] | None = None
    ) -> dict[str, Any]:
        """Send a message and track it."""
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
        """Send an event and track it."""
        event = {
            "id": f"evt-{len(self.events_sent)}",
            "content": content,
            "message_type": message_type,
            "metadata": metadata or {},
        }
        self.events_sent.append(event)
        return event

    async def add_participant(self, name: str, role: str = "member") -> dict[str, Any]:
        """Add a participant and track it."""
        participant = {"id": f"p-{name}", "name": name, "role": role}
        self.participants_added.append(participant)
        return participant

    async def remove_participant(self, name: str) -> dict[str, Any]:
        """Remove a participant and track it."""
        participant = {"id": f"p-{name}", "name": name}
        self.participants_removed.append(participant)
        return participant

    async def get_participants(self) -> list[dict[str, Any]]:
        """Get participants (returns empty list by default)."""
        return []

    async def lookup_peers(self, page: int = 1, page_size: int = 50) -> dict[str, Any]:
        """Lookup peers (returns empty result by default)."""
        return {
            "peers": [],
            "metadata": {"page": page, "page_size": page_size, "total": 0},
        }

    async def create_chatroom(self, task_id: str | None = None) -> str:
        """Create a chatroom and return its ID."""
        return f"room-{uuid.uuid4()}"

    def get_tool_schemas(self, format: str) -> list[dict[str, Any]]:
        """Get tool schemas (returns empty list by default)."""
        return []

    def get_anthropic_tool_schemas(self) -> list[dict[str, Any]]:
        """Get Anthropic-formatted tool schemas."""
        return []

    def get_openai_tool_schemas(self) -> list[dict[str, Any]]:
        """Get OpenAI-formatted tool schemas."""
        return []

    async def execute_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Execute a tool call and track it."""
        call = {"tool_name": tool_name, "arguments": arguments}
        self.tool_calls.append(call)
        return {"status": "ok"}
