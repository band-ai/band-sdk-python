"""Tests for Parlant tools module."""

from types import SimpleNamespace
from typing import get_args
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.core.exceptions import BandToolError
from band.core.memory_types import enum_values
from band.core.task_types import TaskAssignmentStatus, TaskLifecycleState
from band.core.types import AdapterFeatures, Capability
from band.integrations.parlant.tools import (
    _session_message_sent,
    _session_tools,
    create_parlant_tools,
    get_current_tools,
    get_session_tools,
    mark_message_sent,
    set_current_tools,
    set_session_tools,
    was_message_sent,
)
from band.runtime.tools import TASK_TOOL_NAMES, TOOL_MODELS, ListContactRequestsInput

try:
    import parlant.sdk  # noqa: F401

    _PARLANT_INSTALLED = True
except ImportError:
    _PARLANT_INSTALLED = False


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
        with pytest.warns(DeprecationWarning, match="set_current_tools is deprecated"):
            set_current_tools(MagicMock())

    def test_get_current_tools_emits_deprecation_warning(self):
        """Should emit deprecation warning."""
        with pytest.warns(DeprecationWarning, match="get_current_tools is deprecated"):
            get_current_tools()

    def test_get_current_tools_returns_none(self):
        """Should return None (tools now accessed via session_id)."""
        with pytest.warns(DeprecationWarning):
            result = get_current_tools()

        assert result is None


@pytest.mark.skipif(
    not _PARLANT_INSTALLED, reason="needs the real parlant SDK (dev-parlant venv)"
)
class TestCreateParlantTools:
    """Tests for create_parlant_tools() function.

    Real parlant, not mocked: ``create_parlant_tools`` builds its tools with the
    real ``@p.tool`` decorator, which introspects each function's signature/
    docstring into a real ``Tool.parameters`` schema — the schema shape *is* what
    these tests verify, so faking the decorator would test the fake, not the
    integration.
    """

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
        assert "band_send_message" in tool_names
        assert "band_send_event" in tool_names
        assert "band_add_participant" in tool_names
        assert "band_remove_participant" in tool_names
        assert "band_lookup_peers" in tool_names
        assert "band_get_participants" in tool_names
        assert "band_create_chatroom" in tool_names
        assert "band_list_contacts" in tool_names
        assert "band_add_contact" in tool_names
        assert "band_remove_contact" in tool_names
        assert "band_list_contact_requests" in tool_names
        assert "band_respond_contact_request" in tool_names
        assert "band_list_room_files" in tool_names
        assert "band_read_room_file" in tool_names
        assert "band_send_room_file" in tool_names

    def test_tools_have_descriptions(self):
        """Should have descriptions for all tools."""
        tools = create_parlant_tools()

        for entry in tools:
            assert entry.tool.description, f"Tool {entry.tool.name} has no description"

    def test_description_reflects_master_model_edit(self, monkeypatch):
        """A master model docstring edit must reach the Parlant tool description.

        Mutates the actual source (``TOOL_MODELS`` docstrings) rather than
        re-deriving the expected text through ``get_tool_description()`` — the
        function under test's own dependency — so this can't pass on a
        hand-written docstring that coincidentally matches today's master text.
        That's the regression this fix closes: Parlant tools used to hand-write
        their own docs instead of reading the master model at all.
        """
        for name, model in TOOL_MODELS.items():
            sentinel = f"SENTINEL DOCSTRING FOR {name}"
            monkeypatch.setattr(model, "__doc__", sentinel)

        tools = create_parlant_tools()
        checked = 0
        for entry in tools:
            if entry.tool.name not in TOOL_MODELS:
                continue
            checked += 1
            assert f"SENTINEL DOCSTRING FOR {entry.tool.name}" in entry.tool.description
        assert checked == len(tools), (
            "expected every Parlant tool to have a master model"
        )

    def test_tool_parameters_have_descriptions(self):
        """Every tool argument should carry a description, not just the tool itself.

        Parlant's schema builder never reads a docstring's Args: section (unlike
        pydantic-ai's griffe parser) — a parameter only gets a description via
        Annotated[T, ToolParameterOptions(description=...)] on its type
        annotation. Without that, every argument silently reaches the LLM with
        no description at all.
        """
        tools = create_parlant_tools()

        missing = [
            (entry.tool.name, param_name)
            for entry in tools
            for param_name, (_, options) in entry.tool.parameters.items()
            if not options.description
        ]
        assert not missing, f"parameters with no description: {missing}"

    def test_parameter_description_reflects_master_model_field_edit(self, monkeypatch):
        """A master field description edit must reach the Parlant parameter schema.

        Same mutation-test shape as test_description_reflects_master_model_edit,
        applied per argument instead of per tool. Scoped to parameters Parlant's
        tool functions actually accept — some master fields (e.g.
        AddParticipantInput.role, SendEventInput.metadata, both LookupPeersInput
        fields) aren't exposed as Parlant parameters at all; the tool hardcodes
        that value internally instead.
        """
        baseline_params = {
            (entry.tool.name, param_name)
            for entry in create_parlant_tools()
            for param_name in entry.tool.parameters
        }

        sentinels: dict[tuple[str, str], str] = {}
        for tool_name, model in TOOL_MODELS.items():
            for field_name, field in model.model_fields.items():
                if (
                    tool_name,
                    field_name,
                ) not in baseline_params or not field.description:
                    continue
                sentinel = f"SENTINEL FIELD DESC FOR {tool_name}.{field_name}"
                monkeypatch.setattr(field, "description", sentinel)
                sentinels[(tool_name, field_name)] = sentinel

        tools = create_parlant_tools()
        checked = 0
        for entry in tools:
            for param_name, (_, options) in entry.tool.parameters.items():
                sentinel = sentinels.get((entry.tool.name, param_name))
                if sentinel is None:
                    continue
                checked += 1
                assert options.description is not None
                assert options.description.startswith(sentinel)
        assert checked == len(sentinels), (
            "expected every field-described master parameter to reach Parlant"
        )

    def test_send_message_mentions_param_notes_comma_separated_shape(self):
        """mentions is a comma-separated string in Parlant, not the master's list[str].

        The per-argument description must say so, not just the tool-level docstring —
        otherwise an LLM asking about this one argument sees the master's
        list-oriented wording unqualified.
        """
        tools = create_parlant_tools()

        send_message_entry = next(
            t for t in tools if t.tool.name == "band_send_message"
        )
        description = send_message_entry.tool.parameters["mentions"][1].description

        assert description is not None
        assert "comma" in description

    def test_list_contact_requests_sent_status_description_lists_literal_choices(self):
        """sent_status's master field is Literal[...]; handing that type to
        Parlant directly crashes tool registration (Parlant's schema builder
        only turns a real enum.Enum into an ``enum``), so its choices must
        reach the LLM as prose in the description instead of vanishing.
        """
        choices = get_args(
            ListContactRequestsInput.model_fields["sent_status"].annotation
        )

        tools = create_parlant_tools(
            features=AdapterFeatures(capabilities={Capability.CONTACTS})
        )
        entry = next(t for t in tools if t.tool.name == "band_list_contact_requests")
        description = entry.tool.parameters["sent_status"][1].description

        assert description is not None
        for choice in choices:
            assert choice in description

    def test_send_message_tool_has_required_parameters(self):
        """send_message should have content and mentions parameters."""
        tools = create_parlant_tools()

        send_message_entry = next(
            t for t in tools if t.tool.name == "band_send_message"
        )
        # Parameters is a dict with param names as keys
        param_names = list(send_message_entry.tool.parameters.keys())

        assert "content" in param_names
        assert "mentions" in param_names

    def test_send_event_tool_has_message_type_parameter(self):
        """send_event should have message_type parameter."""
        tools = create_parlant_tools()

        send_event_entry = next(t for t in tools if t.tool.name == "band_send_event")
        param_names = list(send_event_entry.tool.parameters.keys())

        assert "content" in param_names
        assert "message_type" in param_names

    def test_add_participant_tool_has_identifier_parameter(self):
        """add_participant should have identifier parameter."""
        tools = create_parlant_tools()

        add_participant_entry = next(
            t for t in tools if t.tool.name == "band_add_participant"
        )
        param_names = list(add_participant_entry.tool.parameters.keys())

        assert "identifier" in param_names

    def test_lookup_peers_has_no_parameters(self):
        """lookup_peers should have no user-facing parameters (pagination is hardcoded)."""
        tools = create_parlant_tools()

        lookup_peers_entry = next(
            t for t in tools if t.tool.name == "band_lookup_peers"
        )
        param_names = list(lookup_peers_entry.tool.parameters.keys())

        # Pagination was intentionally removed to simplify the API
        # The function uses hardcoded defaults (page=1, page_size=50)
        assert param_names == []

    def test_excludes_contact_tools_without_capability(self):
        """Contact tools excluded when CONTACTS capability is absent."""
        tools = create_parlant_tools(features=AdapterFeatures())
        tool_names = [t.tool.name for t in tools]

        assert "band_send_message" in tool_names
        assert "band_create_chatroom" in tool_names
        assert "band_list_contacts" not in tool_names
        assert "band_add_contact" not in tool_names
        assert "band_remove_contact" not in tool_names
        assert "band_list_contact_requests" not in tool_names
        assert "band_respond_contact_request" not in tool_names

    def test_excludes_file_tools_without_capability(self):
        """File tools excluded when FILES capability is absent."""
        tools = create_parlant_tools(features=AdapterFeatures())
        tool_names = [t.tool.name for t in tools]

        assert "band_list_room_files" not in tool_names
        assert "band_read_room_file" not in tool_names
        assert "band_send_room_file" not in tool_names

    def test_includes_file_tools_with_capability(self):
        """File tools included when FILES capability is present."""
        tools = create_parlant_tools(
            features=AdapterFeatures(capabilities={Capability.FILES})
        )
        tool_names = [t.tool.name for t in tools]

        assert "band_list_room_files" in tool_names
        assert "band_read_room_file" in tool_names
        assert "band_send_room_file" in tool_names

    def test_includes_file_tools_when_no_features(self):
        """File tools included when features is None (backward compat)."""
        tools = create_parlant_tools(features=None)
        tool_names = [t.tool.name for t in tools]

        assert "band_list_room_files" in tool_names
        assert "band_send_room_file" in tool_names

    def test_send_room_file_mentions_param_notes_comma_separated_shape(self):
        """mentions is a comma-separated string in Parlant, not the master's list[str]."""
        tools = create_parlant_tools()

        entry = next(t for t in tools if t.tool.name == "band_send_room_file")
        description = entry.tool.parameters["mentions"][1].description

        assert description is not None
        assert "comma" in description

    def test_read_room_file_tool_has_file_id_parameter(self):
        """read_room_file should have a file_id parameter."""
        tools = create_parlant_tools()

        entry = next(t for t in tools if t.tool.name == "band_read_room_file")
        param_names = list(entry.tool.parameters.keys())

        assert "file_id" in param_names

    def test_includes_contact_tools_with_capability(self):
        """Contact tools included when CONTACTS capability is present."""
        tools = create_parlant_tools(
            features=AdapterFeatures(capabilities={Capability.CONTACTS})
        )
        tool_names = [t.tool.name for t in tools]

        assert "band_list_contacts" in tool_names
        assert "band_add_contact" in tool_names
        assert "band_remove_contact" in tool_names
        assert "band_list_contact_requests" in tool_names
        assert "band_respond_contact_request" in tool_names

    def test_includes_contact_tools_when_no_features(self):
        """Contact tools included when features is None (backward compat)."""
        tools = create_parlant_tools(features=None)
        tool_names = [t.tool.name for t in tools]

        assert "band_list_contacts" in tool_names
        assert "band_respond_contact_request" in tool_names

    def test_excludes_task_tools_without_capability(self):
        """Task tools excluded when TASKS capability is absent."""
        tools = create_parlant_tools(features=AdapterFeatures())
        tool_names = {t.tool.name for t in tools}

        assert "band_send_message" in tool_names
        assert not TASK_TOOL_NAMES & tool_names

    def test_includes_task_tools_with_capability(self):
        """Task tools included when TASKS capability is present."""
        tools = create_parlant_tools(
            features=AdapterFeatures(capabilities={Capability.TASKS})
        )
        tool_names = {t.tool.name for t in tools}

        assert TASK_TOOL_NAMES <= tool_names

    def test_update_task_status_and_state_are_real_enums(self):
        """status/state are real StrEnum fields, so Parlant renders them as a
        JSON-Schema enum directly -- no Literal-choices-in-prose fallback needed.
        """
        tools = create_parlant_tools(
            features=AdapterFeatures(capabilities={Capability.TASKS})
        )
        entry = next(t for t in tools if t.tool.name == "band_update_task")

        status_schema = entry.tool.parameters["status"][0]
        state_schema = entry.tool.parameters["state"][0]

        assert set(status_schema["enum"]) == set(enum_values(TaskAssignmentStatus))
        assert set(state_schema["enum"]) == set(enum_values(TaskLifecycleState))


@pytest.mark.skipif(
    not _PARLANT_INSTALLED, reason="needs the real parlant SDK (dev-parlant venv)"
)
class TestParlantToolFunctions:
    """Tests for individual Parlant tool functions.

    Drives the real tools built by ``create_parlant_tools`` (see that class's
    docstring for why this needs real parlant, not a fake).
    """

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
                "data": [
                    {"name": "Agent1", "description": "Test agent", "type": "Agent"}
                ],
                "metadata": {"page": 1, "total_pages": 1},
            }
        )
        tools.get_participants = AsyncMock(
            return_value=[{"name": "User1", "type": "User"}]
        )
        tools.create_chatroom = AsyncMock(return_value="new-room-123")
        tools.list_room_files = AsyncMock(
            return_value={
                "data": [
                    {
                        "id": "file-1",
                        "name": "report.txt",
                        "content_type": "text/plain",
                        "bytes": 42,
                    }
                ],
                "next_cursor": None,
            }
        )
        tools.read_room_file = AsyncMock(
            return_value={
                "name": "report.txt",
                "content_type": "text/plain",
                "bytes": 42,
                "text": "hello world",
            }
        )
        tools.send_room_file = AsyncMock(
            return_value={
                "attachment": {"id": "file-2", "name": "notes.txt"},
                "message_id": "msg-1",
            }
        )
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

        send_message = parlant_tools["band_send_message"]
        result = await send_message(mock_context, "Hello world", "Alice, Bob")

        mock_tools.send_message.assert_called_once_with("Hello world", ["Alice", "Bob"])
        assert "Message sent to Alice, Bob" in result.data

    @pytest.mark.asyncio
    async def test_send_message_marks_message_sent(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should mark message as sent after successful send."""
        set_session_tools(mock_context.session_id, mock_tools)

        send_message = parlant_tools["band_send_message"]
        await send_message(mock_context, "Hello", "Alice")

        assert was_message_sent(mock_context.session_id) is True

    @pytest.mark.asyncio
    async def test_send_message_returns_error_without_tools(
        self, parlant_tools, mock_context
    ):
        """Should return error when no tools available."""
        send_message = parlant_tools["band_send_message"]
        result = await send_message(mock_context, "Hello", "Alice")

        assert "Error: No tools available" in result.data

    @pytest.mark.asyncio
    async def test_send_message_requires_mentions(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should return error when no mentions provided."""
        mock_tools.agent_id = "self"
        mock_tools.participants = [
            {"id": "user-1", "handle": "@alice"},
            {"id": "self", "handle": "@self"},
        ]
        set_session_tools(mock_context.session_id, mock_tools)

        send_message = parlant_tools["band_send_message"]
        result = await send_message(mock_context, "Hello", "")

        assert "At least one mention is required" in result.data
        assert "@alice" in result.data
        assert "@self" not in result.data

    @pytest.mark.asyncio
    async def test_send_message_translates_band_tool_error(
        self, parlant_tools, mock_tools, mock_context
    ):
        """BandToolError from underlying tool must surface as ToolResult, not crash.

        Pins the wrapper translation contract: framework wrappers must catch
        BandToolError raised by AgentTools and return a model-visible
        failure value so the LLM can recover, instead of letting the exception
        crash the turn.
        """
        mock_tools.send_message.side_effect = BandToolError(
            "Backend rejected message: 503 Service Unavailable"
        )
        set_session_tools(mock_context.session_id, mock_tools)

        send_message = parlant_tools["band_send_message"]
        # Must NOT raise — wrapper translates the exception to a tool failure
        result = await send_message(mock_context, "Hello", "Alice")

        # Result is a ToolResult with the error text visible to the LLM
        assert "Error sending message" in result.data
        assert "503" in result.data

    @pytest.mark.asyncio
    async def test_send_message_mention_hint_survives_session_teardown_race(
        self, parlant_tools, mock_tools, mock_context
    ):
        """A room torn down between the tool body's own lookup and the
        mention-hint failure handler's re-lookup must not crash the call.

        guard_failures re-fetches session tools independently when building
        the mention hint; if the session vanished in between, that re-fetch
        returns None and must fall back to the plain error, not attribute
        error out on None.participants.
        """
        set_session_tools(mock_context.session_id, mock_tools)

        def _fail_and_tear_down_session(*args, **kwargs):
            set_session_tools(mock_context.session_id, None)
            raise BandToolError("Backend rejected message: 503 Service Unavailable")

        mock_tools.send_message.side_effect = _fail_and_tear_down_session

        send_message = parlant_tools["band_send_message"]
        # Must NOT raise AttributeError from None.participants
        result = await send_message(mock_context, "Hello", "Alice")

        assert "Error sending message" in result.data
        assert "503" in result.data

    @pytest.mark.asyncio
    async def test_send_event_calls_tools_send_event(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should call tools.send_event with correct parameters."""
        set_session_tools(mock_context.session_id, mock_tools)

        send_event = parlant_tools["band_send_event"]
        result = await send_event(mock_context, "Thinking...", "thought")

        mock_tools.send_event.assert_called_once_with("Thinking...", "thought", None)
        assert "Event (thought) sent successfully" in result.data

    @pytest.mark.asyncio
    async def test_send_event_validates_message_type(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should reject invalid message types."""
        set_session_tools(mock_context.session_id, mock_tools)

        send_event = parlant_tools["band_send_event"]
        result = await send_event(mock_context, "Test", "invalid_type")

        assert "Invalid message_type" in result.data

    @pytest.mark.asyncio
    async def test_add_participant_calls_tools(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should call tools.add_participant."""
        set_session_tools(mock_context.session_id, mock_tools)

        add_participant = parlant_tools["band_add_participant"]
        result = await add_participant(mock_context, "Research Agent")

        mock_tools.add_participant.assert_called_once_with("Research Agent", "member")
        assert "Successfully added 'Research Agent'" in result.data

    @pytest.mark.asyncio
    async def test_remove_participant_calls_tools(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should call tools.remove_participant."""
        set_session_tools(mock_context.session_id, mock_tools)

        remove_participant = parlant_tools["band_remove_participant"]
        result = await remove_participant(mock_context, "Research Agent")

        mock_tools.remove_participant.assert_called_once_with("Research Agent")
        assert "Successfully removed 'Research Agent'" in result.data

    @pytest.mark.asyncio
    async def test_lookup_peers_returns_formatted_list(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should return formatted list of peers."""
        set_session_tools(mock_context.session_id, mock_tools)

        lookup_peers = parlant_tools["band_lookup_peers"]
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
        mock_tools.lookup_peers.return_value = {"data": [], "metadata": {}}
        set_session_tools(mock_context.session_id, mock_tools)

        lookup_peers = parlant_tools["band_lookup_peers"]
        result = await lookup_peers(mock_context)

        assert "No available agents found" in result.data

    @pytest.mark.asyncio
    async def test_get_participants_returns_formatted_list(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should return formatted list of participants."""
        set_session_tools(mock_context.session_id, mock_tools)

        get_participants = parlant_tools["band_get_participants"]
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

        get_participants = parlant_tools["band_get_participants"]
        result = await get_participants(mock_context)

        assert "No participants in the room" in result.data

    @pytest.mark.asyncio
    async def test_create_chatroom_calls_tools(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should call tools.create_chatroom."""
        set_session_tools(mock_context.session_id, mock_tools)

        create_chatroom = parlant_tools["band_create_chatroom"]
        result = await create_chatroom(mock_context, "task-456")

        mock_tools.create_chatroom.assert_called_once_with("task-456")
        assert "Created new chat room: new-room-123" in result.data

    @pytest.mark.asyncio
    async def test_create_chatroom_handles_empty_task_id(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should handle empty task_id."""
        set_session_tools(mock_context.session_id, mock_tools)

        create_chatroom = parlant_tools["band_create_chatroom"]
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

        send_message = parlant_tools["band_send_message"]
        result = await send_message(mock_context, "Hello", "Alice")

        assert "Error sending message: Connection failed" in result.data

    @pytest.mark.asyncio
    async def test_tool_returns_error_on_malformed_call_arguments(
        self, parlant_tools, mock_context
    ):
        """A signature mismatch in guard_failures's own bind() step -- before
        the call-handling try starts, so there's no call.arguments yet to
        build the usual failure message from -- must return a ToolResult,
        not propagate a raw TypeError out of the tool coroutine."""
        send_message = parlant_tools["band_send_message"]

        result = await send_message(mock_context)  # missing content/mentions

        assert result.data.startswith("Error calling band_send_message:")

    @pytest.mark.asyncio
    async def test_tool_logs_result_on_success(
        self, parlant_tools, mock_tools, mock_context, caplog
    ):
        """guard_failures must log a per-call outcome, not just the initial
        'called' line -- operators grep these logs for tool-level
        confirmation (e.g. that a specific branch was hit)."""
        set_session_tools(mock_context.session_id, mock_tools)
        send_message = parlant_tools["band_send_message"]

        with caplog.at_level("INFO"):
            await send_message(mock_context, "Hello", "Alice")

        result_logs = [
            r
            for r in caplog.records
            if r.getMessage().startswith("[Parlant Tool] band_send_message ->")
        ]
        assert len(result_logs) == 1

    @pytest.mark.asyncio
    async def test_list_room_files_returns_formatted_list(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should return formatted list of room files."""
        set_session_tools(mock_context.session_id, mock_tools)

        list_room_files = parlant_tools["band_list_room_files"]
        result = await list_room_files(mock_context, "")

        mock_tools.list_room_files.assert_called_once_with(None)
        assert "report.txt" in result.data
        assert "file-1" in result.data

    @pytest.mark.asyncio
    async def test_list_room_files_passes_cursor(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should forward a non-empty cursor to the underlying tool."""
        set_session_tools(mock_context.session_id, mock_tools)

        list_room_files = parlant_tools["band_list_room_files"]
        await list_room_files(mock_context, "cursor-1")

        mock_tools.list_room_files.assert_called_once_with("cursor-1")

    @pytest.mark.asyncio
    async def test_list_room_files_handles_empty_result(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should handle an empty room-file list."""
        mock_tools.list_room_files.return_value = {"data": [], "next_cursor": None}
        set_session_tools(mock_context.session_id, mock_tools)

        list_room_files = parlant_tools["band_list_room_files"]
        result = await list_room_files(mock_context, "")

        assert "No files found in this room" in result.data

    @pytest.mark.asyncio
    async def test_read_room_file_returns_text(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should return the file's decoded text."""
        set_session_tools(mock_context.session_id, mock_tools)

        read_room_file = parlant_tools["band_read_room_file"]
        result = await read_room_file(mock_context, "file-1")

        mock_tools.read_room_file.assert_called_once_with("file-1")
        assert "hello world" in result.data

    @pytest.mark.asyncio
    async def test_read_room_file_describes_image_instead_of_inlining(
        self, parlant_tools, mock_tools, mock_context
    ):
        """An image result must be described, not passed through as bytes."""
        mock_tools.read_room_file.return_value = {
            "content": [{"type": "image", "data": "ZmFrZQ==", "mimeType": "image/png"}]
        }
        set_session_tools(mock_context.session_id, mock_tools)

        read_room_file = parlant_tools["band_read_room_file"]
        result = await read_room_file(mock_context, "file-1")

        assert "image/png" in result.data
        assert "ZmFrZQ==" not in result.data

    @pytest.mark.asyncio
    async def test_read_room_file_describes_non_previewable_file(
        self, parlant_tools, mock_tools, mock_context
    ):
        """A too-large/non-previewable file returns its description, not bytes."""
        mock_tools.read_room_file.return_value = {
            "name": "archive.zip",
            "content_type": "application/zip",
            "bytes": 999_999,
            "description": "File not shown inline: exceeds the inline text limit.",
        }
        set_session_tools(mock_context.session_id, mock_tools)

        read_room_file = parlant_tools["band_read_room_file"]
        result = await read_room_file(mock_context, "file-1")

        assert "archive.zip" in result.data
        assert "not shown inline" in result.data

    @pytest.mark.asyncio
    async def test_send_room_file_calls_tools_send_room_file(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should call tools.send_room_file with parsed mentions."""
        set_session_tools(mock_context.session_id, mock_tools)

        send_room_file = parlant_tools["band_send_room_file"]
        result = await send_room_file(
            mock_context, "file body", "notes.txt", "Alice, Bob", "here's a file"
        )

        mock_tools.send_room_file.assert_called_once_with(
            "file body", "notes.txt", "here's a file", ["Alice", "Bob"]
        )
        assert "notes.txt" in result.data
        assert "file-2" in result.data

    @pytest.mark.asyncio
    async def test_send_room_file_requires_mentions(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should return error when no mentions provided."""
        mock_tools.agent_id = "self"
        mock_tools.participants = [
            {"id": "user-1", "handle": "@alice"},
            {"id": "self", "handle": "@self"},
        ]
        set_session_tools(mock_context.session_id, mock_tools)

        send_room_file = parlant_tools["band_send_room_file"]
        result = await send_room_file(mock_context, "body", "notes.txt", "", "")

        assert "At least one mention is required" in result.data
        assert "@alice" in result.data
        mock_tools.send_room_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_room_file_translates_band_tool_error(
        self, parlant_tools, mock_tools, mock_context
    ):
        """BandToolError from underlying tool must surface as ToolResult, not crash."""
        mock_tools.send_room_file.side_effect = BandToolError(
            "Filename must use plain printable ASCII characters"
        )
        set_session_tools(mock_context.session_id, mock_tools)

        send_room_file = parlant_tools["band_send_room_file"]
        result = await send_room_file(mock_context, "body", "café.txt", "Alice", "")

        assert "Error sending room file" in result.data
        assert "ASCII" in result.data
