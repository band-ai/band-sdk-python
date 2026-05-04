"""Tests for FakeAgentTools testing utility."""

from thenvoi.core.protocols import AgentToolsProtocol
from thenvoi.testing import FakeAgentTools


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
        await tools.execute_tool_call("thenvoi_send_message", {"content": "Hi"})
        await tools.execute_tool_call(
            "thenvoi_add_participant", {"identifier": "Alice"}
        )

        # Verify tool calls were made
        assert len(tools.tool_calls) == 2
        assert tools.tool_calls[0]["tool_name"] == "thenvoi_send_message"
        assert tools.tool_calls[1]["tool_name"] == "thenvoi_add_participant"
