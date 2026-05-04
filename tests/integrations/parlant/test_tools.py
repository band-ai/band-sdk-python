"""Tests for Parlant tools module."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from thenvoi.integrations.parlant.tools import (
    _session_message_sent,
    _session_tools,
    create_parlant_tools,
    get_session_tools,
    mark_message_sent,
    set_session_tools,
    was_message_sent,
)


class TestSessionToolsRegistry:
    """Tests for session-keyed tools registry."""

    def setup_method(self):
        """Clear registry before each test."""
        _session_tools.clear()
        _session_message_sent.clear()

    def test_set_session_tools_stores_tools(self):
        """Should store tools for a session."""
        mock_tools = MagicMock()

        set_session_tools("session-123", mock_tools)

        assert "session-123" in _session_tools
        assert _session_tools["session-123"] is mock_tools

    def test_set_session_tools_initializes_message_sent_flag(self):
        """Should initialize message_sent flag to False."""
        mock_tools = MagicMock()

        set_session_tools("session-123", mock_tools)

        assert _session_message_sent["session-123"] is False

    def test_set_session_tools_clears_on_none(self):
        """Should clear tools when setting None."""
        mock_tools = MagicMock()
        set_session_tools("session-123", mock_tools)
        assert "session-123" in _session_tools

        set_session_tools("session-123", None)

        assert "session-123" not in _session_tools
        assert "session-123" not in _session_message_sent

    def test_get_session_tools_returns_stored_tools(self):
        """Should return stored tools for session."""
        mock_tools = MagicMock()
        _session_tools["session-123"] = mock_tools

        result = get_session_tools("session-123")

        assert result is mock_tools

    def test_get_session_tools_returns_none_for_unknown_session(self):
        """Should return None for unknown session."""
        result = get_session_tools("unknown-session")

        assert result is None


class TestMessageSentFlag:
    """Tests for message sent tracking."""

    def setup_method(self):
        """Clear registry before each test."""
        _session_tools.clear()
        _session_message_sent.clear()

    def test_mark_message_sent_sets_flag(self):
        """Should set message_sent flag to True."""
        _session_message_sent["session-123"] = False

        mark_message_sent("session-123")

        assert _session_message_sent["session-123"] is True

    def test_was_message_sent_returns_true_when_sent(self):
        """Should return True when message was sent."""
        _session_message_sent["session-123"] = True

        result = was_message_sent("session-123")

        assert result is True

    def test_was_message_sent_returns_false_when_not_sent(self):
        """Should return False when message was not sent."""
        _session_message_sent["session-123"] = False

        result = was_message_sent("session-123")

        assert result is False

    def test_was_message_sent_returns_false_for_unknown_session(self):
        """Should return False for unknown session."""
        result = was_message_sent("unknown-session")

        assert result is False


class TestDeprecatedFunctions:
    """Tests for deprecated compatibility functions."""

    def test_set_current_tools_emits_deprecation_warning(self):
        """Should emit deprecation warning."""
        from thenvoi.integrations.parlant.tools import set_current_tools

        with pytest.warns(DeprecationWarning, match="set_current_tools is deprecated"):
            set_current_tools(MagicMock())

    def test_get_current_tools_emits_deprecation_warning(self):
        """Should emit deprecation warning."""
        from thenvoi.integrations.parlant.tools import get_current_tools

        with pytest.warns(DeprecationWarning, match="get_current_tools is deprecated"):
            get_current_tools()

    def test_get_current_tools_returns_none(self):
        """Should return None (tools now accessed via session_id)."""
        from thenvoi.integrations.parlant.tools import get_current_tools

        with pytest.warns(DeprecationWarning):
            result = get_current_tools()

        assert result is None


class TestCreateParlantTools:
    """Tests for create_parlant_tools() function."""

    def test_returns_list_of_tools(self):
        """Should return list of tool entries when Parlant is installed."""
        tools = create_parlant_tools()

        assert isinstance(tools, list)
        # Non-empty; specific tool names are verified in the next test.
        # Avoid hardcoded counts so adding/removing tools doesn't silently
        # break this assertion — the next test validates the exact contract.
        assert len(tools) > 0

    def test_returns_expected_tool_names(self):
        """Should return tools with expected names."""
        tools = create_parlant_tools()

        # Tools are ToolEntry objects with a .tool attribute containing the Tool
        tool_names = [t.tool.name for t in tools]
        assert "thenvoi_send_message" in tool_names
        assert "thenvoi_send_event" in tool_names
        assert "thenvoi_add_participant" in tool_names
        assert "thenvoi_remove_participant" in tool_names
        assert "thenvoi_lookup_peers" in tool_names
        assert "thenvoi_get_participants" in tool_names
        assert "thenvoi_create_chatroom" in tool_names
        assert "thenvoi_list_contacts" in tool_names
        assert "thenvoi_add_contact" in tool_names
        assert "thenvoi_remove_contact" in tool_names
        assert "thenvoi_list_contact_requests" in tool_names
        assert "thenvoi_respond_contact_request" in tool_names

    def test_tools_have_descriptions(self):
        """Should have descriptions for all tools."""
        tools = create_parlant_tools()

        for entry in tools:
            assert entry.tool.description, f"Tool {entry.tool.name} has no description"

    def test_send_message_tool_has_required_parameters(self):
        """send_message should have content and mentions parameters."""
        tools = create_parlant_tools()

        send_message_entry = next(
            t for t in tools if t.tool.name == "thenvoi_send_message"
        )
        # Parameters is a dict with param names as keys
        param_names = list(send_message_entry.tool.parameters.keys())

        assert "content" in param_names
        assert "mentions" in param_names

    def test_send_event_tool_has_message_type_parameter(self):
        """send_event should have message_type parameter."""
        tools = create_parlant_tools()

        send_event_entry = next(t for t in tools if t.tool.name == "thenvoi_send_event")
        param_names = list(send_event_entry.tool.parameters.keys())

        assert "content" in param_names
        assert "message_type" in param_names

    def test_add_participant_tool_has_identifier_parameter(self):
        """add_participant should have identifier parameter."""
        tools = create_parlant_tools()

        add_participant_entry = next(
            t for t in tools if t.tool.name == "thenvoi_add_participant"
        )
        param_names = list(add_participant_entry.tool.parameters.keys())

        assert "identifier" in param_names

    def test_lookup_peers_has_no_parameters(self):
        """lookup_peers should have no user-facing parameters (pagination is hardcoded)."""
        tools = create_parlant_tools()

        lookup_peers_entry = next(
            t for t in tools if t.tool.name == "thenvoi_lookup_peers"
        )
        param_names = list(lookup_peers_entry.tool.parameters.keys())

        # Pagination was intentionally removed to simplify the API
        # The function uses hardcoded defaults (page=1, page_size=50)
        assert param_names == []

    def test_excludes_contact_tools_without_capability(self):
        """Contact tools excluded when CONTACTS capability is absent."""
        from thenvoi.core.types import AdapterFeatures

        tools = create_parlant_tools(features=AdapterFeatures())
        tool_names = [t.tool.name for t in tools]

        assert "thenvoi_send_message" in tool_names
        assert "thenvoi_create_chatroom" in tool_names
        assert "thenvoi_list_contacts" not in tool_names
        assert "thenvoi_add_contact" not in tool_names
        assert "thenvoi_remove_contact" not in tool_names
        assert "thenvoi_list_contact_requests" not in tool_names
        assert "thenvoi_respond_contact_request" not in tool_names

    def test_includes_contact_tools_with_capability(self):
        """Contact tools included when CONTACTS capability is present."""
        from thenvoi.core.types import AdapterFeatures, Capability

        tools = create_parlant_tools(
            features=AdapterFeatures(capabilities={Capability.CONTACTS})
        )
        tool_names = [t.tool.name for t in tools]

        assert "thenvoi_list_contacts" in tool_names
        assert "thenvoi_add_contact" in tool_names
        assert "thenvoi_remove_contact" in tool_names
        assert "thenvoi_list_contact_requests" in tool_names
        assert "thenvoi_respond_contact_request" in tool_names

    def test_includes_contact_tools_when_no_features(self):
        """Contact tools included when features is None (backward compat)."""
        tools = create_parlant_tools(features=None)
        tool_names = [t.tool.name for t in tools]

        assert "thenvoi_list_contacts" in tool_names
        assert "thenvoi_respond_contact_request" in tool_names


class TestParlantToolFunctions:
    """Tests for individual Parlant tool functions."""

    def setup_method(self):
        """Clear registry and set up mocks before each test."""
        _session_tools.clear()
        _session_message_sent.clear()

    @pytest.fixture
    def mock_tools(self):
        """Create mock AgentToolsProtocol (MagicMock base, AsyncMock methods)."""
        tools = MagicMock()
        tools.send_message = AsyncMock()
        tools.send_event = AsyncMock()
        tools.add_participant = AsyncMock(return_value={"status": "added"})
        tools.remove_participant = AsyncMock()
        tools.lookup_peers = AsyncMock(
            return_value={
                "peers": [
                    {"name": "Agent1", "description": "Test agent", "type": "Agent"}
                ],
                "metadata": {"page": 1, "total_pages": 1},
            }
        )
        tools.get_participants = AsyncMock(
            return_value=[{"name": "User1", "type": "User"}]
        )
        tools.create_chatroom = AsyncMock(return_value="new-room-123")
        return tools

    @pytest.fixture
    def mock_context(self):
        """Create mock ToolContext.

        Uses ``SimpleNamespace`` so that accessing any attribute not
        explicitly set raises ``AttributeError`` — this catches tests
        that accidentally depend on attributes beyond ``session_id``.
        ``MagicMock(spec=ToolContext)`` is not used because ``ToolContext``
        lives in ``parlant.core.tools`` which may not be installed.
        """
        from types import SimpleNamespace

        return SimpleNamespace(session_id="test-session-123")

    @pytest.fixture
    def parlant_tools(self):
        """Create Parlant tools from the real create_parlant_tools."""
        tools = create_parlant_tools()
        # Build a dict mapping tool name to the tool's function
        return {entry.tool.name: entry.function for entry in tools}

    @pytest.mark.asyncio
    async def test_send_message_calls_tools_send_message(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should call tools.send_message with parsed mentions."""
        set_session_tools(mock_context.session_id, mock_tools)

        send_message = parlant_tools["thenvoi_send_message"]
        result = await send_message(mock_context, "Hello world", "Alice, Bob")

        mock_tools.send_message.assert_called_once_with("Hello world", ["Alice", "Bob"])
        assert "Message sent to Alice, Bob" in result.data

    @pytest.mark.asyncio
    async def test_send_message_marks_message_sent(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should mark message as sent after successful send."""
        set_session_tools(mock_context.session_id, mock_tools)

        send_message = parlant_tools["thenvoi_send_message"]
        await send_message(mock_context, "Hello", "Alice")

        assert was_message_sent(mock_context.session_id) is True

    @pytest.mark.asyncio
    async def test_send_message_returns_error_without_tools(
        self, parlant_tools, mock_context
    ):
        """Should return error when no tools available."""
        send_message = parlant_tools["thenvoi_send_message"]
        result = await send_message(mock_context, "Hello", "Alice")

        assert "Error: No tools available" in result.data

    @pytest.mark.asyncio
    async def test_send_message_requires_mentions(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should return error when no mentions provided."""
        set_session_tools(mock_context.session_id, mock_tools)

        send_message = parlant_tools["thenvoi_send_message"]
        result = await send_message(mock_context, "Hello", "")

        assert "At least one mention is required" in result.data

    @pytest.mark.asyncio
    async def test_send_message_translates_thenvoi_tool_error(
        self, parlant_tools, mock_tools, mock_context
    ):
        """ThenvoiToolError from underlying tool must surface as ToolResult, not crash.

        Pins the wrapper translation contract: framework wrappers must catch
        ThenvoiToolError raised by AgentTools and return a model-visible
        failure value so the LLM can recover, instead of letting the exception
        crash the turn.
        """
        from thenvoi.core.exceptions import ThenvoiToolError

        mock_tools.send_message.side_effect = ThenvoiToolError(
            "Backend rejected message: 503 Service Unavailable"
        )
        set_session_tools(mock_context.session_id, mock_tools)

        send_message = parlant_tools["thenvoi_send_message"]
        # Must NOT raise — wrapper translates the exception to a tool failure
        result = await send_message(mock_context, "Hello", "Alice")

        # Result is a ToolResult with the error text visible to the LLM
        assert "Error sending message" in result.data
        assert "503" in result.data

    @pytest.mark.asyncio
    async def test_send_event_calls_tools_send_event(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should call tools.send_event with correct parameters."""
        set_session_tools(mock_context.session_id, mock_tools)

        send_event = parlant_tools["thenvoi_send_event"]
        result = await send_event(mock_context, "Thinking...", "thought")

        mock_tools.send_event.assert_called_once_with("Thinking...", "thought", None)
        assert "Event (thought) sent successfully" in result.data

    @pytest.mark.asyncio
    async def test_send_event_validates_message_type(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should reject invalid message types."""
        set_session_tools(mock_context.session_id, mock_tools)

        send_event = parlant_tools["thenvoi_send_event"]
        result = await send_event(mock_context, "Test", "invalid_type")

        assert "Invalid message_type" in result.data

    @pytest.mark.asyncio
    async def test_add_participant_calls_tools(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should call tools.add_participant."""
        set_session_tools(mock_context.session_id, mock_tools)

        add_participant = parlant_tools["thenvoi_add_participant"]
        result = await add_participant(mock_context, "Research Agent")

        mock_tools.add_participant.assert_called_once_with("Research Agent", "member")
        assert "Successfully added 'Research Agent'" in result.data

    @pytest.mark.asyncio
    async def test_remove_participant_calls_tools(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should call tools.remove_participant."""
        set_session_tools(mock_context.session_id, mock_tools)

        remove_participant = parlant_tools["thenvoi_remove_participant"]
        result = await remove_participant(mock_context, "Research Agent")

        mock_tools.remove_participant.assert_called_once_with("Research Agent")
        assert "Successfully removed 'Research Agent'" in result.data

    @pytest.mark.asyncio
    async def test_lookup_peers_returns_formatted_list(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should return formatted list of peers."""
        set_session_tools(mock_context.session_id, mock_tools)

        lookup_peers = parlant_tools["thenvoi_lookup_peers"]
        result = await lookup_peers(mock_context)

        # Pagination is hardcoded in the implementation (page=1, page_size=50)
        mock_tools.lookup_peers.assert_called_once_with(page=1, page_size=50)
        assert "Available agents" in result.data
        assert "Agent1" in result.data

    @pytest.mark.asyncio
    async def test_lookup_peers_handles_empty_result(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should handle empty peers list."""
        mock_tools.lookup_peers.return_value = {"peers": [], "metadata": {}}
        set_session_tools(mock_context.session_id, mock_tools)

        lookup_peers = parlant_tools["thenvoi_lookup_peers"]
        result = await lookup_peers(mock_context)

        assert "No available agents found" in result.data

    @pytest.mark.asyncio
    async def test_get_participants_returns_formatted_list(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should return formatted list of participants."""
        set_session_tools(mock_context.session_id, mock_tools)

        get_participants = parlant_tools["thenvoi_get_participants"]
        result = await get_participants(mock_context)

        mock_tools.get_participants.assert_called_once()
        assert "Current participants" in result.data
        assert "User1" in result.data

    @pytest.mark.asyncio
    async def test_get_participants_handles_empty_room(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should handle empty participants list."""
        mock_tools.get_participants.return_value = []
        set_session_tools(mock_context.session_id, mock_tools)

        get_participants = parlant_tools["thenvoi_get_participants"]
        result = await get_participants(mock_context)

        assert "No participants in the room" in result.data

    @pytest.mark.asyncio
    async def test_create_chatroom_calls_tools(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should call tools.create_chatroom."""
        set_session_tools(mock_context.session_id, mock_tools)

        create_chatroom = parlant_tools["thenvoi_create_chatroom"]
        result = await create_chatroom(mock_context, "task-456")

        mock_tools.create_chatroom.assert_called_once_with("task-456")
        assert "Created new chat room: new-room-123" in result.data

    @pytest.mark.asyncio
    async def test_create_chatroom_handles_empty_task_id(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should handle empty task_id."""
        set_session_tools(mock_context.session_id, mock_tools)

        create_chatroom = parlant_tools["thenvoi_create_chatroom"]
        result = await create_chatroom(mock_context, "")

        mock_tools.create_chatroom.assert_called_once_with(None)
        assert "Created new chat room" in result.data

    @pytest.mark.asyncio
    async def test_tool_handles_exception(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should return error message when tool raises exception."""
        mock_tools.send_message.side_effect = Exception("Connection failed")
        set_session_tools(mock_context.session_id, mock_tools)

        send_message = parlant_tools["thenvoi_send_message"]
        result = await send_message(mock_context, "Hello", "Alice")

        assert "Error sending message: Connection failed" in result.data
