"""Tests for FakeAgentTools testing utility."""

from typing import Any

from band.core.protocols import AgentToolsProtocol
from band.runtime.tools import serialize_tool_result
from band.testing import FakeAgentTools


async def store_fact(tools: FakeAgentTools, content: str) -> None:
    """Store a memory with the platform-required fields filled in."""
    await tools.store_memory(
        content=content,
        system="long_term",
        type="semantic",
        segment="user",
        thought="noted",
        scope="organization",
    )


async def listing_seen_by_adapter(
    tools: FakeAgentTools, **kwargs: Any
) -> dict[str, Any]:
    """The serialized envelope an adapter receives from band_list_memories."""
    return serialize_tool_result(await tools.list_memories(**kwargs))


def listed_contents(listing: dict[str, Any]) -> list[str]:
    """Each listed memory's content, in listing order."""
    return [memory["content"] for memory in listing["data"]]


class TestMemoryListing:
    """list_memories must serve the real SDK's Fern envelope (data/meta)."""

    async def test_stored_memories_come_back_in_the_real_envelope(self):
        tools = FakeAgentTools()
        await store_fact(tools, "prefers dark mode")

        listing = await listing_seen_by_adapter(tools)

        assert set(listing) == {"data", "meta"}, (
            f"Envelope keys {set(listing)} drifted from the real SDK's "
            "{data, meta} — adapters reading .data/.meta would go untested"
        )
        assert listed_contents(listing) == ["prefers dark mode"], (
            "A stored memory must be visible in the listing's data"
        )
        assert listing["meta"] == {"page_size": 1, "total_count": 1}, (
            "meta must report this page's size and the total match count"
        )

    async def test_page_size_serves_the_first_page(self):
        tools = FakeAgentTools()
        for content in ("first", "second", "third"):
            await store_fact(tools, content)

        listing = await listing_seen_by_adapter(tools, page_size=2)

        assert listed_contents(listing) == ["first", "second"], (
            "page_size must truncate to the first page, oldest first"
        )
        assert listing["meta"] == {"page_size": 2, "total_count": 3}, (
            "meta.page_size is the served page's size, "
            "total_count the whole store — the platform's semantics"
        )

    async def test_seeded_memories_are_listed(self):
        seeded = {
            "id": "mem-1",
            "content": "seeded fact",
            "system": "long_term",
            "type": "semantic",
            "segment": "user",
            "scope": "organization",
            "inserted_at": "2025-01-01T00:00:00Z",
        }
        tools = FakeAgentTools(memories=[seeded])

        listing = await listing_seen_by_adapter(tools)

        assert listed_contents(listing) == ["seeded fact"], (
            "Constructor-seeded memories must be served by list_memories, "
            "so tests can start from a pre-populated store"
        )


class TestFakeAgentToolsProtocol:
    """Verify FakeAgentTools implements AgentToolsProtocol."""

    def test_implements_protocol(self):
        """FakeAgentTools should be a valid AgentToolsProtocol."""
        tools = FakeAgentTools()
        assert isinstance(tools, AgentToolsProtocol)


class TestSendMessage:
    """Tests for send_message tracking."""

    async def test_tracks_sent_messages(self):
        """Should track all sent messages."""
        tools = FakeAgentTools()

        result = await tools.send_message(content="Hello!")

        assert len(tools.messages_sent) == 1
        assert tools.messages_sent[0]["content"] == "Hello!"
        assert result["content"] == "Hello!"

    async def test_tracks_mentions(self):
        """Should track mentions in sent messages."""
        tools = FakeAgentTools()

        await tools.send_message(content="Hi @user", mentions=["user-1", "user-2"])

        assert tools.messages_sent[0]["mentions"] == ["user-1", "user-2"]

    async def test_generates_unique_ids(self):
        """Should generate unique IDs for each message."""
        tools = FakeAgentTools()

        await tools.send_message(content="First")
        await tools.send_message(content="Second")

        assert tools.messages_sent[0]["id"] == "msg-0"
        assert tools.messages_sent[1]["id"] == "msg-1"


class TestSendEvent:
    """Tests for send_event tracking."""

    async def test_tracks_sent_events(self):
        """Should track all sent events."""
        tools = FakeAgentTools()

        result = await tools.send_event(content="Thinking...", message_type="thought")

        assert len(tools.events_sent) == 1
        assert tools.events_sent[0]["content"] == "Thinking..."
        assert tools.events_sent[0]["message_type"] == "thought"
        assert result["message_type"] == "thought"

    async def test_tracks_metadata(self):
        """Should track metadata in sent events."""
        tools = FakeAgentTools()

        await tools.send_event(
            content="Tool call",
            message_type="tool_call",
            metadata={"tool_name": "search"},
        )

        assert tools.events_sent[0]["metadata"] == {"tool_name": "search"}


class TestParticipantOperations:
    """Tests for participant tracking."""

    async def test_tracks_added_participants(self):
        """Should track added participants."""
        tools = FakeAgentTools()

        result = await tools.add_participant(identifier="Alice", role="admin")

        assert len(tools.participants_added) == 1
        assert tools.participants_added[0]["name"] == "Alice"
        assert tools.participants_added[0]["role"] == "admin"
        assert result["name"] == "Alice"
        assert tools.participants == [
            {"id": "p-Alice", "name": "Alice", "role": "admin", "handle": "Alice"}
        ]

    async def test_tracks_removed_participants(self):
        """Should track removed participants."""
        tools = FakeAgentTools()

        result = await tools.remove_participant(identifier="Bob")

        assert len(tools.participants_removed) == 1
        assert tools.participants_removed[0]["name"] == "Bob"
        assert result["name"] == "Bob"

    async def test_get_participants_returns_empty(self):
        """Should return empty list by default."""
        tools = FakeAgentTools()

        result = await tools.get_participants()

        assert result == []


class TestLookupPeers:
    """Tests for lookup_peers."""

    async def test_returns_empty_peers(self):
        """Should return empty peers list with metadata."""
        tools = FakeAgentTools()

        result = await tools.lookup_peers(page=2, page_size=25)

        assert result["peers"] == []
        assert result["metadata"]["page"] == 2
        assert result["metadata"]["page_size"] == 25
        assert result["metadata"]["total"] == 0


class TestCreateChatroom:
    """Tests for create_chatroom."""

    async def test_returns_room_id(self):
        """Should return a generated room ID."""
        tools = FakeAgentTools()

        result = await tools.create_chatroom(task_id="task-123")

        assert result.startswith("room-")

    async def test_returns_room_id_without_task_id(self):
        """Should return a generated room ID when no task_id provided."""
        tools = FakeAgentTools()

        result = await tools.create_chatroom()

        assert result.startswith("room-")


class TestToolSchemas:
    """Tests for get_tool_schemas."""

    def test_returns_empty_schemas(self):
        """Should return empty schemas by default."""
        tools = FakeAgentTools()

        result = tools.get_tool_schemas(format="openai")

        assert result == []


class TestExecuteToolCall:
    """Tests for execute_tool_call tracking."""

    async def test_tracks_tool_calls(self):
        """Should track all tool calls."""
        tools = FakeAgentTools()

        result = await tools.execute_tool_call(
            tool_name="search", arguments={"query": "hello"}
        )

        assert len(tools.tool_calls) == 1
        assert tools.tool_calls[0]["tool_name"] == "search"
        assert tools.tool_calls[0]["arguments"] == {"query": "hello"}
        assert result == {"status": "ok"}

    async def test_tracks_multiple_tool_calls(self):
        """Should track multiple tool calls in order."""
        tools = FakeAgentTools()

        await tools.execute_tool_call("tool1", {"a": 1})
        await tools.execute_tool_call("tool2", {"b": 2})
        await tools.execute_tool_call("tool3", {"c": 3})

        assert len(tools.tool_calls) == 3
        assert tools.tool_calls[0]["tool_name"] == "tool1"
        assert tools.tool_calls[1]["tool_name"] == "tool2"
        assert tools.tool_calls[2]["tool_name"] == "tool3"


class TestUsageInAdapterTests:
    """Integration-style tests showing FakeAgentTools usage pattern."""

    async def test_adapter_test_pattern(self):
        """Demonstrate the testing pattern for adapters."""
        # This is how you'd use FakeAgentTools in adapter tests
        tools = FakeAgentTools()

        # Simulate adapter behavior
        await tools.send_event(content="Starting...", message_type="thought")
        await tools.send_message(content="Hello, user!")
        await tools.send_event(content="Done", message_type="thought")

        # Assertions
        assert len(tools.events_sent) == 2
        assert len(tools.messages_sent) == 1
        assert tools.messages_sent[0]["content"] == "Hello, user!"

    async def test_tool_execution_pattern(self):
        """Demonstrate tool execution testing pattern."""
        tools = FakeAgentTools()

        # Simulate LLM tool calls
        await tools.execute_tool_call("band_send_message", {"content": "Hi"})
        await tools.execute_tool_call("band_add_participant", {"identifier": "Alice"})

        # Verify tool calls were made
        assert len(tools.tool_calls) == 2
        assert tools.tool_calls[0]["tool_name"] == "band_send_message"
        assert tools.tool_calls[1]["tool_name"] == "band_add_participant"
