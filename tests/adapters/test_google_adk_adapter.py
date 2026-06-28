"""Tests for GoogleADKAdapter.

Tests for shared adapter behavior (initialization defaults, custom kwargs,
history_converter, on_started agent_name/description, on_message callable,
cleanup safety) live in tests/framework_conformance/test_adapter_conformance.py.
This file contains Google ADK-specific behavior: system prompt rendering,
session management, tool bridging, custom tools, and error handling.
"""

from __future__ import annotations

import asyncio
import importlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, Field
from band.core.types import PlatformMessage

pytest.importorskip("google.adk", reason="google-adk not installed")

# Imports below require google-adk; guarded by importorskip above.
_google_adk_mod = importlib.import_module("band.adapters.google_adk")
GoogleADKAdapter = _google_adk_mod.GoogleADKAdapter
_get_tool_bridge_class = _google_adk_mod._get_tool_bridge_class
_BandToolBridge = _get_tool_bridge_class()
_sanitize_adk_agent_name = _google_adk_mod._sanitize_adk_agent_name
_strip_additional_properties = _google_adk_mod._strip_additional_properties


@pytest.fixture
def sample_message():
    """Create a sample platform message."""
    return PlatformMessage(
        id="msg-123",
        room_id="room-123",
        content="Hello, agent!",
        sender_id="user-456",
        sender_type="User",
        sender_name="Alice",
        message_type="text",
        metadata={},
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def mock_tools():
    """Create mock AgentToolsProtocol (MagicMock base, AsyncMock methods)."""
    tools = MagicMock()
    tools.get_tool_schemas = MagicMock(return_value=[])
    tools.get_openai_tool_schemas = MagicMock(
        return_value=[
            {
                "type": "function",
                "function": {
                    "name": "band_send_message",
                    "description": "Send a message",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "mentions": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["content", "mentions"],
                    },
                },
            }
        ]
    )
    tools.send_message = AsyncMock(return_value={"status": "sent"})
    tools.send_event = AsyncMock(return_value={"status": "sent"})
    tools.execute_tool_call = AsyncMock(return_value={"status": "success"})
    return tools


class TestInitialization:
    """Tests for adapter initialization."""

    def test_default_model(self):
        """Should default to gemini-2.5-flash."""
        adapter = GoogleADKAdapter()
        assert adapter.model == "gemini-2.5-flash"

    def test_custom_model(self):
        """Should accept custom model."""
        adapter = GoogleADKAdapter(model="gemini-2.5-pro")
        assert adapter.model == "gemini-2.5-pro"

    def test_system_prompt_override(self):
        """Should store custom system_prompt override."""
        adapter = GoogleADKAdapter(system_prompt="You are a custom assistant.")
        assert adapter._system_prompt_override == "You are a custom assistant."

    def test_custom_section(self):
        """Should store custom section."""
        adapter = GoogleADKAdapter(custom_section="Be helpful.")
        assert adapter.custom_section == "Be helpful."

    def test_execution_reporting_default(self):
        """Should default execution reporting to False."""
        from band.core.types import Emit

        adapter = GoogleADKAdapter()
        assert Emit.EXECUTION not in adapter.features.emit

    def test_memory_tools_default(self):
        """Should default memory tools to False."""
        from band.core.types import Capability

        adapter = GoogleADKAdapter()
        assert Capability.MEMORY not in adapter.features.capabilities

    def test_empty_custom_tools(self):
        """Should default to empty custom tools list."""
        adapter = GoogleADKAdapter()
        assert adapter._custom_tools == []

    def test_history_converter_set(self):
        """Should have history converter set by default."""
        adapter = GoogleADKAdapter()
        assert adapter.history_converter is not None

    def test_max_history_messages_default(self):
        """Should default max_history_messages to 50."""
        adapter = GoogleADKAdapter()
        assert adapter.max_history_messages == 50

    def test_custom_max_history_messages(self):
        """Should accept custom max_history_messages."""
        adapter = GoogleADKAdapter(max_history_messages=100)
        assert adapter.max_history_messages == 100


class TestADKAgentNameSanitization:
    """Tests for ADK-safe internal agent names."""

    def test_replaces_spaces_and_punctuation(self):
        """Display names with punctuation should become valid ADK identifiers."""
        assert _sanitize_adk_agent_name("Band Agent!") == "Band_Agent_"

    def test_prefixes_digit_leading_names(self):
        """ADK rejects identifiers that start with digits, even after punctuation replacement."""
        safe_name = _sanitize_adk_agent_name("24/7 Support")

        assert safe_name == "band_24_7_Support"
        assert safe_name.isidentifier()

    def test_avoids_reserved_user_name(self):
        """ADK reserves the exact agent name 'user' for end-user input."""
        assert _sanitize_adk_agent_name("user") == "band_user"

    def test_uses_default_for_empty_name(self):
        """Empty names should still produce the default ADK-safe identifier."""
        assert _sanitize_adk_agent_name("") == "band_agent"


class TestOnStarted:
    """Tests for on_started() method."""

    @pytest.mark.asyncio
    async def test_renders_system_prompt(self):
        """Should render system prompt from agent metadata."""
        adapter = GoogleADKAdapter()
        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")

        assert adapter._system_prompt != ""
        assert "TestBot" in adapter._system_prompt

    @pytest.mark.asyncio
    async def test_uses_custom_system_prompt_when_provided(self):
        """Should use custom system_prompt instead of rendered one."""
        adapter = GoogleADKAdapter(system_prompt="Custom prompt here.")
        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")

        assert adapter._system_prompt == "Custom prompt here."

    @pytest.mark.asyncio
    async def test_sets_agent_name(self):
        """Should set agent_name on the adapter."""
        adapter = GoogleADKAdapter()
        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")

        assert adapter.agent_name == "TestBot"
        assert adapter.agent_description == "A test bot"


class TestOnMessage:
    """Tests for on_message() method."""

    @pytest.mark.asyncio
    async def test_creates_session_on_bootstrap(self, sample_message, mock_tools):
        """Should create a new session on first message."""
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()
            mock_runner.run_async = _empty_async_iter
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            assert "room-123" in adapter._room_sessions

    @pytest.mark.asyncio
    async def test_creates_new_session_on_each_message(
        self, sample_message, mock_tools
    ):
        """Should create a fresh session per message (runner is ephemeral)."""
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()
            mock_runner.run_async = _empty_async_iter
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            # First message (bootstrap)
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            session_id_1 = adapter._room_sessions["room-123"]

            # Second message (not bootstrap)
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-123",
            )

            session_id_2 = adapter._room_sessions["room-123"]
            assert session_id_1 != session_id_2

    @pytest.mark.asyncio
    async def test_injects_participants_message(self, sample_message, mock_tools):
        """Should include participants update in message."""
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")

        captured_messages = []

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()

            def capture_run(**kwargs):
                captured_messages.append(kwargs.get("new_message"))
                return _empty_async_iter()

            mock_runner.run_async = capture_run
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg="Alice joined the room",
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

        assert len(captured_messages) == 1
        content = captured_messages[0]
        text = content.parts[0].text
        assert "[System]: Alice joined the room" in text

    @pytest.mark.asyncio
    async def test_injects_history_on_bootstrap(self, sample_message, mock_tools):
        """Should inject history transcript on bootstrap."""
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")

        history = [
            {"role": "user", "content": "[Bob]: Previous message"},
            {"role": "model", "content": "Previous response"},
        ]

        captured_messages = []

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()

            def capture_run(**kwargs):
                captured_messages.append(kwargs.get("new_message"))
                return _empty_async_iter()

            mock_runner.run_async = capture_run
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=history,
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

        assert len(captured_messages) == 1
        text = captured_messages[0].parts[0].text
        assert "Previous conversation context" in text
        assert "[Bob]: Previous message" in text


class TestOnCleanup:
    """Tests for on_cleanup() method."""

    @pytest.mark.asyncio
    async def test_cleans_up_room_session(self):
        """Should remove room session on cleanup."""
        adapter = GoogleADKAdapter()
        adapter._room_sessions["room-123"] = "session-abc"

        await adapter.on_cleanup("room-123")

        assert "room-123" not in adapter._room_sessions

    @pytest.mark.asyncio
    async def test_cleanup_nonexistent_room(self):
        """Should not raise when cleaning up nonexistent room."""
        adapter = GoogleADKAdapter()
        await adapter.on_cleanup("nonexistent-room")

    @pytest.mark.asyncio
    async def test_cleanup_called_twice(self):
        """Should not raise when cleanup is called twice for the same room."""
        adapter = GoogleADKAdapter()
        adapter._room_history["room-123"] = [{"role": "user", "content": "hi"}]
        adapter._room_sessions["room-123"] = "session-abc"

        await adapter.on_cleanup("room-123")
        await adapter.on_cleanup("room-123")

        assert "room-123" not in adapter._room_history
        assert "room-123" not in adapter._room_sessions

    @pytest.mark.asyncio
    async def test_cleanup_one_room_while_another_active(self):
        """Should only clean up the specified room, leaving others untouched."""
        adapter = GoogleADKAdapter()
        adapter._room_history["room-A"] = [{"role": "user", "content": "A"}]
        adapter._room_sessions["room-A"] = "session-A"
        adapter._room_history["room-B"] = [{"role": "user", "content": "B"}]
        adapter._room_sessions["room-B"] = "session-B"

        await adapter.on_cleanup("room-A")

        assert "room-A" not in adapter._room_history
        assert "room-A" not in adapter._room_sessions
        assert adapter._room_history["room-B"] == [{"role": "user", "content": "B"}]
        assert adapter._room_sessions["room-B"] == "session-B"


class TestToolBridge:
    """Tests for _BandToolBridge."""

    def test_tool_name(self):
        """Should return the tool name."""
        bridge = _BandToolBridge(
            tool_name="band_send_message",
            tool_description="Send a message",
            parameters_schema={},
            tools=MagicMock(),
            custom_tools=[],
        )
        assert bridge.name == "band_send_message"

    def test_tool_description(self):
        """Should return the tool description."""
        bridge = _BandToolBridge(
            tool_name="test_tool",
            tool_description="A test tool",
            parameters_schema={},
            tools=MagicMock(),
            custom_tools=[],
        )
        assert bridge.description == "A test tool"

    def test_get_declaration(self):
        """Should build a FunctionDeclaration with stripped additionalProperties."""
        bridge = _BandToolBridge(
            tool_name="my_tool",
            tool_description="Does things",
            parameters_schema={
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "additionalProperties": False,
            },
            tools=MagicMock(),
            custom_tools=[],
        )

        decl = bridge._get_declaration()

        assert decl.name == "my_tool"
        assert decl.description == "Does things"
        # additionalProperties should be stripped for Gemini compatibility
        assert "additionalProperties" not in (decl.parameters or {})

    def test_declaration_eagerly_cached(self):
        """Declaration should be built eagerly in __init__ and cached."""
        bridge = _BandToolBridge(
            tool_name="cached_tool",
            tool_description="Cached",
            parameters_schema={"type": "object", "properties": {}},
            tools=MagicMock(),
            custom_tools=[],
        )

        # _cached_declaration should exist from __init__
        assert hasattr(bridge, "_cached_declaration")
        assert bridge._cached_declaration.name == "cached_tool"

        # _build_declaration should return the same cached object
        assert bridge._build_declaration() is bridge._cached_declaration

        # _get_declaration should delegate to _build_declaration
        assert bridge._get_declaration() is bridge._cached_declaration

    @pytest.mark.asyncio
    async def test_executes_platform_tool(self):
        """Should delegate to AgentToolsProtocol."""
        mock_tools = MagicMock()
        mock_tools.execute_tool_call = AsyncMock(return_value={"status": "sent"})

        bridge = _BandToolBridge(
            tool_name="band_send_message",
            tool_description="Send a message",
            parameters_schema={},
            tools=mock_tools,
            custom_tools=[],
        )

        result = await bridge.run_async(
            args={"content": "Hello", "mentions": ["@alice"]},
            tool_context=MagicMock(),
        )

        mock_tools.execute_tool_call.assert_called_once_with(
            "band_send_message", {"content": "Hello", "mentions": ["@alice"]}
        )
        assert result == '{"status": "sent"}'

    @pytest.mark.asyncio
    async def test_handles_tool_error(self):
        """Should return error string on tool failure."""
        mock_tools = MagicMock()
        mock_tools.execute_tool_call = AsyncMock(side_effect=Exception("Tool failed!"))

        bridge = _BandToolBridge(
            tool_name="failing_tool",
            tool_description="Fails",
            parameters_schema={},
            tools=mock_tools,
            custom_tools=[],
        )

        result = await bridge.run_async(args={}, tool_context=MagicMock())
        assert "Error" in result
        assert "Tool failed!" in result


class TestDeclarationCandidateDetection:
    """Tests for _DECLARATION_CANDIDATES probing and smoke-test."""

    def test_no_candidates_raises_runtime_error(self):
        """Should raise RuntimeError when BaseTool has no known declaration method."""
        _get_tool_bridge_class.cache_clear()
        try:
            with patch.object(
                _google_adk_mod,
                "_DECLARATION_CANDIDATES",
                ("_nonexistent_method",),
            ):
                _get_tool_bridge_class.cache_clear()
                with pytest.raises(RuntimeError, match="no known declaration method"):
                    _get_tool_bridge_class()
        finally:
            _get_tool_bridge_class.cache_clear()
            # Re-populate cache with valid class
            _get_tool_bridge_class()

    def test_smoke_test_runs_at_class_creation(self):
        """Smoke-test should verify declaration mechanism end-to-end."""
        # The fact that _get_tool_bridge_class() returned successfully at
        # module level (line 26) proves the smoke-test passed.  Verify the
        # returned class has the expected declaration method.
        bridge = _BandToolBridge(
            tool_name="smoke",
            tool_description="smoke test",
            parameters_schema={},
            tools=MagicMock(),
            custom_tools=[],
        )
        decl = bridge._get_declaration()
        assert decl is not None
        assert decl.name == "smoke"


class TestStripAdditionalProperties:
    """Tests for _strip_additional_properties module-level function."""

    def test_strips_additional_properties(self):
        """Should strip additionalProperties from schema for Gemini compatibility."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "nested": {
                    "type": "object",
                    "properties": {"x": {"type": "integer"}},
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
            "required": ["name"],
        }

        cleaned = _strip_additional_properties(schema)

        assert "additionalProperties" not in cleaned
        assert "additionalProperties" not in cleaned["properties"]["nested"]
        assert cleaned["properties"]["name"] == {"type": "string"}
        assert cleaned["required"] == ["name"]

    def test_handles_top_level_list(self):
        """Should recurse into top-level list items (e.g. anyOf/oneOf schemas)."""
        schema_list = [
            {"type": "string", "additionalProperties": False},
            {
                "type": "object",
                "properties": {"x": {"type": "integer"}},
                "additionalProperties": False,
            },
        ]

        cleaned = _strip_additional_properties(schema_list)

        assert isinstance(cleaned, list)
        assert len(cleaned) == 2
        assert "additionalProperties" not in cleaned[0]
        assert "additionalProperties" not in cleaned[1]
        assert cleaned[1]["properties"]["x"] == {"type": "integer"}

    def test_handles_non_dict_input(self):
        """Should return non-dict/non-list input as-is."""
        assert _strip_additional_properties("string") == "string"
        assert _strip_additional_properties(42) == 42
        assert _strip_additional_properties(None) is None


class TestBuildADKTools:
    """Tests for _build_adk_tools."""

    def test_builds_platform_tools(self, mock_tools):
        """Should create tool bridges from platform tool schemas."""
        adapter = GoogleADKAdapter()
        bridges = adapter._build_adk_tools(mock_tools)

        assert len(bridges) == 1
        assert bridges[0].name == "band_send_message"

    def test_includes_custom_tools(self, mock_tools):
        """Should include custom tools in the bridge list."""

        class EchoInput(BaseModel):
            """Echo back the message."""

            message: str = Field(description="Message to echo")

        async def echo(args: EchoInput) -> str:
            return f"Echo: {args.message}"

        adapter = GoogleADKAdapter(additional_tools=[(EchoInput, echo)])
        bridges = adapter._build_adk_tools(mock_tools)

        assert len(bridges) == 2
        tool_names = [b.name for b in bridges]
        assert "band_send_message" in tool_names
        assert "echo" in tool_names


class TestCustomTools:
    """Tests for custom tool support."""

    def test_accepts_additional_tools_parameter(self):
        """Adapter should accept list of (Model, func) tuples."""

        class EchoInput(BaseModel):
            """Echo back the message."""

            message: str = Field(description="Message to echo")

        async def echo(args: EchoInput) -> str:
            return f"Echo: {args.message}"

        adapter = GoogleADKAdapter(additional_tools=[(EchoInput, echo)])
        assert len(adapter._custom_tools) == 1

    @pytest.mark.asyncio
    async def test_custom_tool_execution_via_bridge(self):
        """Custom tool should be executed via bridge."""

        class EchoInput(BaseModel):
            """Echo back the message."""

            message: str = Field(description="Message to echo")

        async def echo(args: EchoInput) -> str:
            return f"Echo: {args.message}"

        mock_tools = MagicMock()
        mock_tools.execute_tool_call = AsyncMock()

        bridge = _BandToolBridge(
            tool_name="echo",
            tool_description="Echo back the message",
            parameters_schema={},
            tools=mock_tools,
            custom_tools=[(EchoInput, echo)],
        )

        result = await bridge.run_async(
            args={"message": "Hello"},
            tool_context=MagicMock(),
        )

        # Should NOT have called platform tool
        mock_tools.execute_tool_call.assert_not_called()
        assert "Echo: Hello" in result

    @pytest.mark.asyncio
    async def test_sync_custom_tool_execution(self):
        """Sync custom tool should be executed via bridge."""

        class CalcInput(BaseModel):
            """Calculate sum."""

            a: int = Field(description="First number")
            b: int = Field(description="Second number")

        def calculate(args: CalcInput) -> str:
            return f"Result: {args.a + args.b}"

        mock_tools = MagicMock()
        mock_tools.execute_tool_call = AsyncMock()

        # Tool name derived from model: CalcInput -> "calc"
        bridge = _BandToolBridge(
            tool_name="calc",
            tool_description="Calculate sum",
            parameters_schema={},
            tools=mock_tools,
            custom_tools=[(CalcInput, calculate)],
        )

        result = await bridge.run_async(
            args={"a": 3, "b": 4},
            tool_context=MagicMock(),
        )

        mock_tools.execute_tool_call.assert_not_called()
        assert "Result: 7" in result

    @pytest.mark.asyncio
    async def test_custom_tool_validation_error(self):
        """Should return formatted validation error for invalid arguments."""

        class StrictInput(BaseModel):
            """Requires a name field."""

            name: str = Field(description="Required name")

        async def strict_tool(args: StrictInput) -> str:
            return f"Hello, {args.name}"

        mock_tools = MagicMock()
        mock_tools.execute_tool_call = AsyncMock()

        bridge = _BandToolBridge(
            tool_name="strict",
            tool_description="Requires name",
            parameters_schema={},
            tools=mock_tools,
            custom_tools=[(StrictInput, strict_tool)],
        )

        # Pass empty args - missing required 'name' field
        result = await bridge.run_async(
            args={},
            tool_context=MagicMock(),
        )

        assert "Invalid arguments" in result
        assert "name" in result

    @pytest.mark.asyncio
    async def test_multiple_custom_tools_execution(self):
        """Multiple custom tools should be independently executable."""

        class EchoInput(BaseModel):
            """Echo back the message."""

            message: str = Field(description="Message to echo")

        class CalcInput(BaseModel):
            """Calculate sum."""

            a: int = Field(description="First number")
            b: int = Field(description="Second number")

        async def echo(args: EchoInput) -> str:
            return f"Echo: {args.message}"

        def calculate(args: CalcInput) -> str:
            return f"Sum: {args.a + args.b}"

        custom_tools = [(EchoInput, echo), (CalcInput, calculate)]
        mock_tools = MagicMock()
        mock_tools.execute_tool_call = AsyncMock()

        echo_bridge = _BandToolBridge(
            tool_name="echo",
            tool_description="Echo",
            parameters_schema={},
            tools=mock_tools,
            custom_tools=custom_tools,
        )
        # Tool name derived from model: CalcInput -> "calc"
        calc_bridge = _BandToolBridge(
            tool_name="calc",
            tool_description="Calc",
            parameters_schema={},
            tools=mock_tools,
            custom_tools=custom_tools,
        )

        echo_result = await echo_bridge.run_async(
            args={"message": "Hi"}, tool_context=MagicMock()
        )
        calc_result = await calc_bridge.run_async(
            args={"a": 2, "b": 3}, tool_context=MagicMock()
        )

        assert "Echo: Hi" in echo_result
        assert "Sum: 5" in calc_result
        mock_tools.execute_tool_call.assert_not_called()


class TestContactsInjection:
    """Tests for contacts_msg injection."""

    @pytest.mark.asyncio
    async def test_injects_contacts_message(self, sample_message, mock_tools):
        """Should include contacts broadcast in message."""
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")

        captured_messages = []

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()

            def capture_run(**kwargs):
                captured_messages.append(kwargs.get("new_message"))
                return _empty_async_iter()

            mock_runner.run_async = capture_run
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg="Bob is now a contact",
                is_session_bootstrap=True,
                room_id="room-123",
            )

        assert len(captured_messages) == 1
        text = captured_messages[0].parts[0].text
        assert "[System]: Bob is now a contact" in text


class TestExecutionReporting:
    """Tests for _report_event execution reporting."""

    @pytest.mark.asyncio
    async def test_reports_function_calls(self, mock_tools):
        """Should report function calls as tool_call events."""
        adapter = GoogleADKAdapter(enable_execution_reporting=True)

        mock_fc = MagicMock()
        mock_fc.name = "band_send_message"
        mock_fc.args = {"content": "Hello"}
        mock_fc.id = "fc-1"

        event = MagicMock()
        event.get_function_calls.return_value = [mock_fc]
        event.get_function_responses.return_value = []

        await adapter._report_event(event, mock_tools)

        mock_tools.send_event.assert_called_once()
        call_args = mock_tools.send_event.call_args
        assert call_args.kwargs["message_type"] == "tool_call"
        assert "band_send_message" in call_args.kwargs["content"]

    @pytest.mark.asyncio
    async def test_reports_function_responses(self, mock_tools):
        """Should report function responses as tool_result events."""
        adapter = GoogleADKAdapter(enable_execution_reporting=True)

        mock_fr = MagicMock()
        mock_fr.name = "band_send_message"
        mock_fr.response = {"status": "sent"}
        mock_fr.id = "fc-1"

        event = MagicMock()
        event.get_function_calls.return_value = []
        event.get_function_responses.return_value = [mock_fr]

        await adapter._report_event(event, mock_tools)

        mock_tools.send_event.assert_called_once()
        call_args = mock_tools.send_event.call_args
        assert call_args.kwargs["message_type"] == "tool_result"
        assert "band_send_message" in call_args.kwargs["content"]

    @pytest.mark.asyncio
    async def test_skips_event_without_function_methods(self, mock_tools):
        """Should skip events that lack function call/response methods."""
        adapter = GoogleADKAdapter(enable_execution_reporting=True)

        event = MagicMock(spec=[])  # No attributes

        await adapter._report_event(event, mock_tools)

        mock_tools.send_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_report_failure_gracefully(self, mock_tools):
        """Should not raise when event reporting fails."""
        adapter = GoogleADKAdapter(enable_execution_reporting=True)
        mock_tools.send_event = AsyncMock(side_effect=Exception("Network error"))

        mock_fc = MagicMock()
        mock_fc.name = "test_tool"
        mock_fc.args = {}
        mock_fc.id = "fc-1"

        event = MagicMock()
        event.get_function_calls.return_value = [mock_fc]
        event.get_function_responses.return_value = []

        # Should not raise
        await adapter._report_event(event, mock_tools)


class TestErrorHandling:
    """Tests for error handling."""

    @pytest.mark.asyncio
    async def test_reports_error_on_runner_failure(self, sample_message, mock_tools):
        """Should report error when ADK runner fails."""
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()

            async def failing_run(**kwargs):
                raise Exception("Runner Error")
                yield  # noqa: B901 - yield after raise to make async generator

            mock_runner.run_async = failing_run
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            with pytest.raises(Exception, match="Runner Error"):
                await adapter.on_message(
                    msg=sample_message,
                    tools=mock_tools,
                    history=[],
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=True,
                    room_id="room-123",
                )

            mock_tools.send_event.assert_called()
            # Runner should be closed even on error (via finally)
            mock_runner.close.assert_called_once()


class TestHistoryTranscript:
    """Tests for _format_history_transcript."""

    def test_formats_text_messages(self):
        """Should format text messages."""
        adapter = GoogleADKAdapter()
        history = [
            {"role": "user", "content": "[Alice]: Hello"},
            {"role": "user", "content": "[Bob]: Hi there"},
        ]

        transcript = adapter._format_history_transcript(history)

        assert "[Alice]: Hello" in transcript
        assert "[Bob]: Hi there" in transcript

    def test_formats_tool_calls(self):
        """Should format tool call blocks."""
        adapter = GoogleADKAdapter()
        history = [
            {
                "role": "model",
                "content": [
                    {
                        "type": "function_call",
                        "name": "band_send_message",
                        "args": {"content": "Hello"},
                    }
                ],
            }
        ]

        transcript = adapter._format_history_transcript(history)

        assert "[Tool Call]" in transcript
        assert "band_send_message" in transcript

    def test_formats_tool_results(self):
        """Should format tool result blocks."""
        adapter = GoogleADKAdapter()
        history = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "function_response",
                        "name": "band_send_message",
                        "output": '{"status": "sent"}',
                    }
                ],
            }
        ]

        transcript = adapter._format_history_transcript(history)

        assert "[Tool Result]" in transcript
        assert "band_send_message" in transcript

    def test_empty_history(self):
        """Should return empty string for empty history."""
        adapter = GoogleADKAdapter()
        assert adapter._format_history_transcript([]) == ""

    @pytest.mark.asyncio
    async def test_labels_own_agent_replies_with_agent_name(self):
        """Own-agent text (role='model', bare content) should be labeled with the agent's name.

        INT-509 regression: without a speaker label, rehydrated transcripts
        showed the agent's prior replies as anonymous lines and the LLM
        could not tell them apart from peer turns, leading it to re-answer
        questions it had already handled after a restart.

        ``SimpleAdapter.on_started`` propagates the agent name into the
        converter, which is the single source of truth for own-vs-peer
        attribution.  This test exercises that wiring end-to-end so a
        future refactor that drops the propagation step would fail here.
        """
        adapter = GoogleADKAdapter()
        await adapter.on_started("ResearchBot", "Test bot")
        history = [
            {"role": "user", "content": "[Alice]: What's the capital of France?"},
            {"role": "model", "content": "Paris."},
            {"role": "user", "content": "[Alice]: Remember pineapple"},
            {"role": "model", "content": "I'll remember 'pineapple'."},
        ]

        transcript = adapter._format_history_transcript(history)

        assert "[Alice]: What's the capital of France?" in transcript
        assert "[ResearchBot]: Paris." in transcript
        assert "[ResearchBot]: I'll remember 'pineapple'." in transcript


class TestHistoryAccumulation:
    """Tests for per-room history accumulation across messages."""

    @pytest.mark.asyncio
    async def test_non_bootstrap_uses_accumulated_history(
        self, sample_message, mock_tools
    ):
        """Non-bootstrap messages should still inject accumulated history."""
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")

        captured_messages = []

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()

            def capture_run(**kwargs):
                captured_messages.append(kwargs.get("new_message"))
                return _empty_async_iter()

            mock_runner.run_async = capture_run
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            # First message (bootstrap) seeds history
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[
                    {"role": "user", "content": "[Bob]: Previous message"},
                ],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            # Second message (non-bootstrap) should still have history
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],  # History param ignored on non-bootstrap
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-123",
            )

        # Second message should contain accumulated history
        assert len(captured_messages) == 2
        text = captured_messages[1].parts[0].text
        assert "Previous conversation context" in text
        # Should also have the first user message accumulated
        assert "Hello, agent!" in text

    @pytest.mark.asyncio
    async def test_non_bootstrap_no_history_injection_when_empty(
        self, sample_message, mock_tools
    ):
        """Non-bootstrap with no prior history should not inject transcript."""
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")

        captured_messages = []

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()

            def capture_run(**kwargs):
                captured_messages.append(kwargs.get("new_message"))
                return _empty_async_iter()

            mock_runner.run_async = capture_run
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            # Bootstrap with empty history
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            # Non-bootstrap - accumulated history only has the first msg
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-123",
            )

        # Second message should have context from first message
        text = captured_messages[1].parts[0].text
        assert "Previous conversation context" in text

    @pytest.mark.asyncio
    async def test_cleanup_removes_room_history(self):
        """on_cleanup should remove accumulated history."""
        adapter = GoogleADKAdapter()
        adapter._room_history["room-123"] = [
            {"role": "user", "content": "test"},
        ]
        adapter._room_sessions["room-123"] = "session-abc"

        await adapter.on_cleanup("room-123")

        assert "room-123" not in adapter._room_history
        assert "room-123" not in adapter._room_sessions

    @pytest.mark.asyncio
    async def test_sliding_window_limits_transcript(self, sample_message, mock_tools):
        """Should only inject the last max_history_messages in the transcript."""
        adapter = GoogleADKAdapter(max_history_messages=3)
        await adapter.on_started("TestBot", "Test bot")

        # Seed room history with more messages than the window
        adapter._room_history["room-123"] = [
            {"role": "user", "content": f"[User]: Message {i}"} for i in range(10)
        ]

        captured_messages = []

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()

            def capture_run(**kwargs):
                captured_messages.append(kwargs.get("new_message"))
                return _empty_async_iter()

            mock_runner.run_async = capture_run
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-123",
            )

        text = captured_messages[0].parts[0].text
        # Only the last 3 messages should be in the transcript
        assert "Message 7" in text
        assert "Message 8" in text
        assert "Message 9" in text
        # Older messages should be excluded
        assert "Message 0" not in text
        assert "Message 6" not in text

    @pytest.mark.asyncio
    async def test_history_trimmed_to_prevent_unbounded_growth(
        self, sample_message, mock_tools
    ):
        """Should trim accumulated history when it exceeds 2x max_history_messages."""
        adapter = GoogleADKAdapter(max_history_messages=3)
        await adapter.on_started("TestBot", "Test bot")

        # Seed room history just below the trim threshold (2 * 3 = 6)
        adapter._room_history["room-123"] = [
            {"role": "user", "content": f"[User]: Message {i}"} for i in range(6)
        ]

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()
            mock_runner.run_async = _empty_async_iter
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-123",
            )

        # 6 seeded + 1 new user message = 7 > threshold (6), triggers trim to 3
        history = adapter._room_history["room-123"]
        assert len(history) == 3
        # Should keep the most recent messages
        assert history[-1]["role"] == "user"
        assert history[-1]["content"] == "[Alice]: Hello, agent!"


class TestTranscriptTruncation:
    """Tests for character-based transcript truncation."""

    @pytest.mark.asyncio
    async def test_truncates_large_transcript(self, sample_message, mock_tools):
        """Should truncate transcript exceeding max_transcript_chars."""
        adapter = GoogleADKAdapter(max_transcript_chars=200)
        await adapter.on_started("TestBot", "Test bot")

        # Seed history with messages that produce a transcript > 200 chars
        adapter._room_history["room-123"] = [
            {"role": "user", "content": f"[User]: Message number {i} with padding"}
            for i in range(20)
        ]

        captured_messages = []

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()

            def capture_run(**kwargs):
                captured_messages.append(kwargs.get("new_message"))
                return _empty_async_iter()

            mock_runner.run_async = capture_run
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-123",
            )

        text = captured_messages[0].parts[0].text
        # The transcript portion (between the markers) should be <= 200 chars
        start = text.find("[Previous conversation context]\n")
        end = text.find("\n[End of previous context]")
        if start != -1 and end != -1:
            transcript_section = text[
                start + len("[Previous conversation context]\n") : end
            ]
            assert len(transcript_section) <= 200
        # Older messages should be dropped, recent ones kept
        assert "Message number 19" in text
        assert "Message number 0" not in text

    @pytest.mark.asyncio
    async def test_no_truncation_when_within_limit(self, sample_message, mock_tools):
        """Should not truncate transcript when within max_transcript_chars."""
        adapter = GoogleADKAdapter(max_transcript_chars=100_000)
        await adapter.on_started("TestBot", "Test bot")

        adapter._room_history["room-123"] = [
            {"role": "user", "content": "[User]: Short msg"},
        ]

        captured_messages = []

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()

            def capture_run(**kwargs):
                captured_messages.append(kwargs.get("new_message"))
                return _empty_async_iter()

            mock_runner.run_async = capture_run
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-123",
            )

        text = captured_messages[0].parts[0].text
        assert "[User]: Short msg" in text


class TestFinalResponseCapture:
    """Tests for capturing final text response from ADK events."""

    @pytest.mark.asyncio
    async def test_captures_final_response_in_history(self, sample_message, mock_tools):
        """Should capture final response text and add to room history."""
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")

        mock_event = MagicMock()
        mock_event.is_final_response.return_value = True
        mock_part = MagicMock()
        mock_part.text = "Here is my response"
        mock_event.content.parts = [mock_part]

        async def iter_with_response(**kwargs):
            yield mock_event

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()
            mock_runner.run_async = iter_with_response
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

        # History should contain both user message and model response
        history = adapter._room_history["room-123"]
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "model"
        assert history[1]["content"] == "Here is my response"

    def test_extract_event_text_with_content(self):
        """Should extract text from event content parts."""
        mock_event = MagicMock()
        mock_part = MagicMock()
        mock_part.text = "response text"
        mock_event.content.parts = [mock_part]

        result = GoogleADKAdapter._extract_event_text(mock_event)
        assert result == "response text"

    def test_extract_event_text_no_content(self):
        """Should return empty string when event has no content."""
        mock_event = MagicMock()
        mock_event.content = None

        result = GoogleADKAdapter._extract_event_text(mock_event)
        assert result == ""

    def test_extract_event_text_no_text_parts(self):
        """Should return empty string when parts have no text."""
        mock_event = MagicMock()
        mock_part = MagicMock()
        mock_part.text = None
        mock_event.content.parts = [mock_part]

        result = GoogleADKAdapter._extract_event_text(mock_event)
        assert result == ""


class TestReportErrorFailure:
    """Tests for _report_error own failure handling."""

    @pytest.mark.asyncio
    async def test_report_error_handles_own_failure(self, mock_tools):
        """Should not raise when _report_error itself fails."""
        adapter = GoogleADKAdapter()
        mock_tools.send_event = AsyncMock(side_effect=Exception("Network down"))

        # Should not raise
        await adapter._report_error(mock_tools, "some error")


class TestConcurrentMessages:
    """Tests for concurrent on_message calls."""

    @pytest.mark.asyncio
    async def test_concurrent_messages_different_rooms(self, mock_tools):
        """Concurrent on_message calls on different rooms should not interfere."""
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")

        msg_a = PlatformMessage(
            id="msg-a",
            room_id="room-a",
            content="Hello from room A",
            sender_id="user-1",
            sender_type="User",
            sender_name="Alice",
            message_type="text",
            metadata={},
            created_at=datetime.now(timezone.utc),
        )
        msg_b = PlatformMessage(
            id="msg-b",
            room_id="room-b",
            content="Hello from room B",
            sender_id="user-2",
            sender_type="User",
            sender_name="Bob",
            message_type="text",
            metadata={},
            created_at=datetime.now(timezone.utc),
        )

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()
            mock_runner.run_async = _empty_async_iter
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            await asyncio.gather(
                adapter.on_message(
                    msg=msg_a,
                    tools=mock_tools,
                    history=[],
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=True,
                    room_id="room-a",
                ),
                adapter.on_message(
                    msg=msg_b,
                    tools=mock_tools,
                    history=[],
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=True,
                    room_id="room-b",
                ),
            )

        # Each room should have its own independent history
        assert "room-a" in adapter._room_history
        assert "room-b" in adapter._room_history
        assert len(adapter._room_history["room-a"]) == 1
        assert len(adapter._room_history["room-b"]) == 1
        assert "room A" in adapter._room_history["room-a"][0]["content"]
        assert "room B" in adapter._room_history["room-b"][0]["content"]

        # Each room should have its own session
        assert adapter._room_sessions["room-a"] != adapter._room_sessions["room-b"]


# Helper for creating empty async iterators in tests
async def _empty_async_iter(**kwargs):
    return
    yield  # Make it an async generator
