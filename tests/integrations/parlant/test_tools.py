"""Tests for Parlant tools module."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from thenvoi.core.types import AdapterFeatures, Capability
from thenvoi.integrations.parlant.tools import (
    _session_contexts,
    create_parlant_tools,
    get_session_tools,
    mark_message_sent,
    set_session_tools,
    was_message_sent,
)
from thenvoi.runtime.tools import iter_tool_definitions


class CalculatorInput(BaseModel):
    """Add one to the provided value."""

    value: int


def calculate(args: CalculatorInput) -> str:
    return str(args.value + 1)


class TestSessionToolsRegistry:
    """Tests for session-keyed tools registry."""

    def setup_method(self):
        """Clear registry before each test."""
        _session_contexts.clear()

    def test_set_session_tools_stores_tools(self):
        """Should store tools for a session."""
        mock_tools = MagicMock()

        set_session_tools("session-123", mock_tools)

        assert "session-123" in _session_contexts
        assert _session_contexts["session-123"].tools is mock_tools

    def test_set_session_tools_initializes_message_sent_flag(self):
        """Should initialize message_sent flag to False."""
        mock_tools = MagicMock()

        set_session_tools("session-123", mock_tools)

        assert _session_contexts["session-123"].message_sent is False

    def test_set_session_tools_clears_on_none(self):
        """Should clear tools when setting None."""
        mock_tools = MagicMock()
        set_session_tools("session-123", mock_tools)
        assert "session-123" in _session_contexts

        set_session_tools("session-123", None)

        assert "session-123" not in _session_contexts

    def test_get_session_tools_returns_stored_tools(self):
        """Should return stored tools for session."""
        mock_tools = MagicMock()
        set_session_tools("session-123", mock_tools)

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
        _session_contexts.clear()

    def test_mark_message_sent_sets_flag(self):
        """Should set message_sent flag to True."""
        set_session_tools("session-123", MagicMock())

        mark_message_sent("session-123")

        assert _session_contexts["session-123"].message_sent is True

    def test_was_message_sent_returns_true_when_sent(self):
        """Should return True when sent."""
        set_session_tools("session-123", MagicMock())
        mark_message_sent("session-123")

        result = was_message_sent("session-123")

        assert result is True

    def test_was_message_sent_returns_false_when_not_sent(self):
        """Should return False when not sent."""
        set_session_tools("session-123", MagicMock())

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
        """Should return None because tools are session-keyed now."""
        from thenvoi.integrations.parlant.tools import get_current_tools

        with pytest.warns(DeprecationWarning):
            result = get_current_tools()

        assert result is None


class TestCreateParlantTools:
    """Tests for create_parlant_tools() function."""

    def test_returns_tool_entries_from_canonical_registry(self):
        """Generated Parlant tools should come from runtime ToolDefinition entries."""
        tools = create_parlant_tools()

        tool_names = [entry.tool.name for entry in tools]
        expected_names = [
            definition.name
            for definition in iter_tool_definitions(
                surface="agent",
                include_memory=False,
                include_contacts=True,
            )
        ]
        assert tool_names == expected_names

    def test_generated_tools_have_canonical_descriptions(self):
        """Every generated Parlant tool should expose the canonical description."""
        tools = create_parlant_tools()

        for entry in tools:
            assert entry.tool.description, f"Tool {entry.tool.name} has no description"

    def test_generated_parameters_match_tool_models(self):
        """Parlant signatures should not drift from canonical Pydantic tool models."""
        entries = {entry.tool.name: entry for entry in create_parlant_tools()}

        for definition in iter_tool_definitions(
            surface="agent",
            include_memory=False,
            include_contacts=True,
        ):
            entry = entries[definition.name]
            expected_params = list(definition.input_model.model_fields)
            expected_required = [
                name
                for name, field in definition.input_model.model_fields.items()
                if field.is_required()
            ]

            assert list(entry.tool.parameters) == expected_params
            assert entry.tool.required == expected_required

    def test_send_message_mentions_parameter_is_array(self):
        """The wrapper should expose mentions as the canonical list field."""
        entry = next(
            entry
            for entry in create_parlant_tools()
            if entry.tool.name == "thenvoi_send_message"
        )

        descriptor, _options = entry.tool.parameters["mentions"]
        assert descriptor["type"] == "array"
        assert descriptor["item_type"] == "string"

    def test_excludes_contact_tools_without_capability(self):
        """Explicit empty capabilities should exclude contact and memory tools."""
        tools = create_parlant_tools(features=AdapterFeatures())
        tool_names = [entry.tool.name for entry in tools]

        assert "thenvoi_send_message" in tool_names
        assert "thenvoi_list_contacts" not in tool_names
        assert "thenvoi_store_memory" not in tool_names

    def test_includes_contact_tools_with_capability(self):
        """Contact tools are exposed when CONTACTS capability is enabled."""
        tools = create_parlant_tools(
            features=AdapterFeatures(capabilities=frozenset({Capability.CONTACTS}))
        )
        tool_names = [entry.tool.name for entry in tools]

        assert "thenvoi_list_contacts" in tool_names
        assert "thenvoi_add_contact" in tool_names
        assert "thenvoi_store_memory" not in tool_names

    def test_includes_memory_tools_with_capability(self):
        """Memory tools are exposed when MEMORY capability is enabled."""
        tools = create_parlant_tools(
            features=AdapterFeatures(capabilities=frozenset({Capability.MEMORY}))
        )
        tool_names = [entry.tool.name for entry in tools]

        assert "thenvoi_store_memory" in tool_names
        assert "thenvoi_get_memory" in tool_names
        assert "thenvoi_list_contacts" not in tool_names

    def test_includes_contact_tools_when_no_features(self):
        """Legacy default direct calls should still expose contact tools."""
        tools = create_parlant_tools()
        tool_names = [entry.tool.name for entry in tools]

        assert "thenvoi_list_contacts" in tool_names
        assert "thenvoi_store_memory" not in tool_names

    def test_include_tools_filters_by_parlant_tool_name(self):
        """include_tools should narrow generated Parlant ToolEntry objects."""
        tools = create_parlant_tools(
            features=AdapterFeatures(include_tools=frozenset({"thenvoi_send_event"}))
        )

        assert [entry.tool.name for entry in tools] == ["thenvoi_send_event"]

    def test_exclude_tools_filters_by_parlant_tool_name(self):
        """exclude_tools should remove generated Parlant ToolEntry objects."""
        tools = create_parlant_tools(
            features=AdapterFeatures(exclude_tools=frozenset({"thenvoi_send_event"}))
        )
        tool_names = [entry.tool.name for entry in tools]

        assert "thenvoi_send_event" not in tool_names
        assert "thenvoi_send_message" in tool_names

    def test_include_categories_filters_generated_tools(self):
        """Category filters should use canonical contact and memory name sets."""
        tools = create_parlant_tools(
            features=AdapterFeatures(
                capabilities=frozenset({Capability.CONTACTS, Capability.MEMORY}),
                include_categories=frozenset({"memory"}),
            )
        )
        tool_names = [entry.tool.name for entry in tools]

        assert "thenvoi_store_memory" in tool_names
        assert "thenvoi_send_message" not in tool_names
        assert "thenvoi_list_contacts" not in tool_names

    def test_includes_additional_custom_tools(self):
        """CustomToolDef tools should be exposed as generated Parlant tools."""
        tools = create_parlant_tools(additional_tools=[(CalculatorInput, calculate)])
        calculator = next(entry for entry in tools if entry.tool.name == "calculator")

        assert calculator.tool.description
        assert list(calculator.tool.parameters) == ["value"]
        assert calculator.tool.required == ["value"]


class TestParlantToolFunctions:
    """Tests for generated Parlant tool wrapper execution."""

    def setup_method(self):
        """Clear registry before each test."""
        _session_contexts.clear()

    @pytest.fixture
    def mock_tools(self):
        """Create mock AgentToolsProtocol."""
        tools = MagicMock()
        tools.execute_tool_call = AsyncMock(return_value={"status": "ok"})
        tools.send_event = AsyncMock(return_value={"status": "sent"})
        return tools

    @pytest.fixture
    def mock_context(self):
        """Create minimal Parlant ToolContext-like object."""
        return SimpleNamespace(session_id="test-session-123")

    @pytest.fixture
    def parlant_tools(self):
        """Create generated Parlant tool functions keyed by tool name."""
        return {entry.tool.name: entry.function for entry in create_parlant_tools()}

    @pytest.mark.asyncio
    async def test_generated_wrapper_calls_execute_tool_call(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Generated wrappers should route through the canonical dispatcher."""
        set_session_tools(mock_context.session_id, mock_tools)

        result = await parlant_tools["thenvoi_send_event"](
            mock_context,
            "Investigating",
            "thought",
            None,
        )

        mock_tools.execute_tool_call.assert_awaited_once_with(
            "thenvoi_send_event",
            {"content": "Investigating", "message_type": "thought", "metadata": None},
        )
        assert result.data == '{"status": "ok"}'

    @pytest.mark.asyncio
    async def test_generated_wrapper_coerces_json_dict_parameters(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Dict fields exposed as strings should be parsed before validation."""
        set_session_tools(mock_context.session_id, mock_tools)

        await parlant_tools["thenvoi_send_event"](
            mock_context,
            "Investigating",
            "thought",
            '{"step": 1}',
        )

        mock_tools.execute_tool_call.assert_awaited_once_with(
            "thenvoi_send_event",
            {
                "content": "Investigating",
                "message_type": "thought",
                "metadata": {"step": 1},
            },
        )

    @pytest.mark.asyncio
    async def test_generated_wrapper_rejects_invalid_json_dict_parameters(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Invalid JSON should be model-visible and not call the platform tool."""
        set_session_tools(mock_context.session_id, mock_tools)

        result = await parlant_tools["thenvoi_send_event"](
            mock_context,
            "Investigating",
            "thought",
            "{bad json",
        )

        mock_tools.execute_tool_call.assert_not_awaited()
        assert "metadata must be valid JSON" in result.data

    @pytest.mark.asyncio
    async def test_send_message_marks_sent_after_success(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Delivery marker should be set only after canonical send_message succeeds."""
        set_session_tools(mock_context.session_id, mock_tools)

        result = await parlant_tools["thenvoi_send_message"](
            mock_context,
            "Hello",
            ["@alice"],
        )

        mock_tools.execute_tool_call.assert_awaited_once_with(
            "thenvoi_send_message",
            {"content": "Hello", "mentions": ["@alice"]},
        )
        assert result.data == '{"status": "ok"}'
        assert was_message_sent(mock_context.session_id) is True

    @pytest.mark.asyncio
    async def test_send_message_does_not_mark_sent_after_tool_error(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Failed send_message wrapper calls must not count as delivery."""
        mock_tools.execute_tool_call.return_value = (
            "Error executing thenvoi_send_message: boom"
        )
        set_session_tools(mock_context.session_id, mock_tools)

        result = await parlant_tools["thenvoi_send_message"](
            mock_context,
            "Hello",
            ["@alice"],
        )

        assert result.data == "Error executing thenvoi_send_message: boom"
        assert was_message_sent(mock_context.session_id) is False

    @pytest.mark.asyncio
    async def test_tool_returns_error_without_session_tools(
        self, parlant_tools, mock_context
    ):
        """Wrapper should return a model-visible error when no session tools exist."""
        result = await parlant_tools["thenvoi_send_message"](
            mock_context,
            "Hello",
            ["@alice"],
        )

        assert result.data == "Error: No tools available in current context"
        assert was_message_sent(mock_context.session_id) is False

    @pytest.mark.asyncio
    async def test_tool_translates_dispatcher_exception(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Unexpected dispatcher exceptions should become model-visible tool errors."""
        mock_tools.execute_tool_call.side_effect = RuntimeError("Connection failed")
        set_session_tools(mock_context.session_id, mock_tools)

        result = await parlant_tools["thenvoi_send_event"](
            mock_context,
            "Investigating",
            "thought",
            None,
        )

        assert "Error executing thenvoi_send_event: Connection failed" in result.data

    @pytest.mark.asyncio
    async def test_additional_custom_tool_executes_with_validation(self, mock_context):
        """Generated custom wrappers should validate through CustomToolDef."""
        tools = {
            entry.tool.name: entry.function
            for entry in create_parlant_tools(
                additional_tools=[(CalculatorInput, calculate)]
            )
        }

        result = await tools["calculator"](mock_context, 41)

        assert result.data == "42"

    @pytest.mark.asyncio
    async def test_additional_custom_tool_returns_validation_error(self, mock_context):
        """Invalid custom tool args should be model-visible."""
        tools = {
            entry.tool.name: entry.function
            for entry in create_parlant_tools(
                additional_tools=[(CalculatorInput, calculate)]
            )
        }

        result = await tools["calculator"](mock_context, "not-an-int")

        assert "Invalid arguments for calculator" in result.data
