"""Tests for AnthropicAdapter.

Tests for shared adapter behavior (initialization defaults, custom kwargs,
history_converter, on_started agent_name/description, on_message callable,
cleanup safety) live in tests/framework_conformance/test_adapter_conformance.py.
This file contains Anthropic-specific behavior: system prompt rendering,
message history management, tool execution, custom tools, and error handling.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, Field

from band.adapters.anthropic import AnthropicAdapter
from band.core.types import AdapterFeatures, Emit, PlatformMessage, TurnUsage


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
    tools.send_message = AsyncMock(return_value={"status": "sent"})
    tools.send_event = AsyncMock(return_value={"status": "sent"})
    tools.execute_tool_call = AsyncMock(return_value={"status": "success"})
    return tools


class TestInitialization:
    """Tests for adapter initialization."""

    def test_system_prompt_override(self):
        """Should use custom system_prompt if provided."""
        adapter = AnthropicAdapter(
            system_prompt="You are a custom assistant.",
        )

        assert adapter.system_prompt == "You are a custom assistant."


class TestOnStarted:
    """Tests for on_started() method."""

    @pytest.mark.asyncio
    async def test_renders_system_prompt(self):
        """Should render system prompt from agent metadata."""
        adapter = AnthropicAdapter()

        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")

        assert adapter._system_prompt != ""
        assert "TestBot" in adapter._system_prompt

    @pytest.mark.asyncio
    async def test_uses_custom_system_prompt_when_provided(self):
        """Should use custom system_prompt instead of rendered one."""
        adapter = AnthropicAdapter(system_prompt="Custom prompt here.")

        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")

        assert adapter._system_prompt == "Custom prompt here."


class TestOnMessage:
    """Tests for on_message() method."""

    @pytest.mark.asyncio
    async def test_initializes_history_on_bootstrap(self, sample_message, mock_tools):
        """Should initialize room history on first message."""
        adapter = AnthropicAdapter()
        await adapter.on_started("TestBot", "Test bot")

        with patch.object(adapter, "_call_anthropic") as mock_call:
            # Create a mock response that ends the conversation
            mock_response = MagicMock()
            mock_response.stop_reason = "end_turn"
            mock_response.content = []
            mock_call.return_value = mock_response

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            assert "room-123" in adapter._message_history
            assert len(adapter._message_history["room-123"]) >= 1

    @pytest.mark.asyncio
    async def test_loads_existing_history(self, sample_message, mock_tools):
        """Should load historical messages on bootstrap."""
        adapter = AnthropicAdapter()
        await adapter.on_started("TestBot", "Test bot")

        existing_history = [
            {"role": "user", "content": "[Bob]: Previous message"},
            {"role": "assistant", "content": "Previous response"},
        ]

        with patch.object(adapter, "_call_anthropic") as mock_call:
            mock_response = MagicMock()
            mock_response.stop_reason = "end_turn"
            mock_response.content = []
            mock_call.return_value = mock_response

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=existing_history,
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            # Should have existing 2 + current message
            assert len(adapter._message_history["room-123"]) >= 3

    @pytest.mark.asyncio
    async def test_injects_participants_message(self, sample_message, mock_tools):
        """Should inject participants update when provided."""
        adapter = AnthropicAdapter()
        await adapter.on_started("TestBot", "Test bot")

        with patch.object(adapter, "_call_anthropic") as mock_call:
            mock_response = MagicMock()
            mock_response.stop_reason = "end_turn"
            mock_response.content = []
            mock_call.return_value = mock_response

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg="Alice joined the room",
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            # Find the participants message in history
            found = any(
                "[System]: Alice joined" in str(m.get("content", ""))
                for m in adapter._message_history["room-123"]
            )
            assert found


class TestOnCleanup:
    """Tests for on_cleanup() method."""

    @pytest.mark.asyncio
    async def test_cleans_up_room_history(self, sample_message, mock_tools):
        """Should remove room history on cleanup."""
        adapter = AnthropicAdapter()
        await adapter.on_started("TestBot", "Test bot")

        # First add some history
        adapter._message_history["room-123"] = [{"role": "user", "content": "test"}]
        assert "room-123" in adapter._message_history

        await adapter.on_cleanup("room-123")

        assert "room-123" not in adapter._message_history


class TestHelperMethods:
    """Tests for internal helper methods."""

    def test_extract_text_content(self):
        """Should extract text from TextBlock content."""
        from anthropic.types import TextBlock

        adapter = AnthropicAdapter()

        content = [
            TextBlock(type="text", text="Hello"),
            TextBlock(type="text", text="World"),
        ]

        result = adapter._extract_text_content(content)

        assert result == "Hello World"

    def test_extract_text_content_empty(self):
        """Should return empty string for empty content."""
        adapter = AnthropicAdapter()

        result = adapter._extract_text_content([])

        assert result == ""

    def test_serialize_content_blocks(self):
        """Should serialize ToolUseBlock and TextBlock."""
        from anthropic.types import TextBlock, ToolUseBlock

        adapter = AnthropicAdapter()

        content = [
            TextBlock(type="text", text="Some text"),
            ToolUseBlock(
                type="tool_use", id="tool-1", name="search", input={"q": "test"}
            ),
        ]

        result = adapter._serialize_content_blocks(content)

        assert len(result) == 2
        assert result[0]["type"] == "text"
        assert result[0]["text"] == "Some text"
        assert result[1]["type"] == "tool_use"
        assert result[1]["name"] == "search"


class TestToolExecution:
    """Tests for tool execution."""

    @pytest.mark.asyncio
    async def test_reports_tool_calls_when_enabled(self, mock_tools):
        """Should send events when execution reporting is enabled."""
        from anthropic.types import ToolUseBlock

        adapter = AnthropicAdapter(enable_execution_reporting=True)

        mock_response = MagicMock()
        mock_response.content = [
            ToolUseBlock(
                type="tool_use",
                id="tool-1",
                name="band_send_message",
                input={"content": "Hello"},
            )
        ]

        mock_tools.execute_tool_call.return_value = {"status": "success"}

        await adapter._process_tool_calls(mock_response, mock_tools)

        # Should have sent tool_call and tool_result events
        assert mock_tools.send_event.call_count == 2

    @pytest.mark.asyncio
    async def test_send_event_403_does_not_crash_tool_execution(self, mock_tools):
        """send_event 403 should not prevent tool from executing."""
        from anthropic.types import ToolUseBlock

        adapter = AnthropicAdapter(enable_execution_reporting=True)

        mock_response = MagicMock()
        mock_response.content = [
            ToolUseBlock(
                type="tool_use",
                id="tool-1",
                name="band_send_message",
                input={"content": "Hello"},
            )
        ]

        # Simulate 403 on event reporting
        mock_tools.send_event.side_effect = Exception("403 Forbidden")
        mock_tools.execute_tool_call.return_value = {"status": "sent"}

        results = await adapter._process_tool_calls(mock_response, mock_tools)

        # Tool should still have executed successfully
        assert len(results) == 1
        assert results[0]["is_error"] is False
        assert "sent" in results[0]["content"]
        mock_tools.execute_tool_call.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_event_failure_logs_warning(self, mock_tools, caplog):
        """send_event failures should be logged as warnings."""
        import logging

        from anthropic.types import ToolUseBlock

        adapter = AnthropicAdapter(enable_execution_reporting=True)

        mock_response = MagicMock()
        mock_response.content = [
            ToolUseBlock(
                type="tool_use",
                id="tool-1",
                name="band_send_message",
                input={"content": "Hello"},
            )
        ]

        mock_tools.send_event.side_effect = Exception("403 Forbidden")
        mock_tools.execute_tool_call.return_value = {"status": "sent"}

        with caplog.at_level(logging.WARNING):
            await adapter._process_tool_calls(mock_response, mock_tools)

        assert "Failed to send tool_call event: 403 Forbidden" in caplog.text
        assert "Failed to send tool_result event: 403 Forbidden" in caplog.text

    @pytest.mark.asyncio
    async def test_usage_from_response_maps_and_sums(self):
        """`_usage_from_response` maps Anthropic usage; TurnUsage `+` sums a loop."""
        first = MagicMock()
        first.usage = MagicMock(
            input_tokens=100,
            output_tokens=20,
            cache_read_input_tokens=5,
            cache_creation_input_tokens=0,
        )
        second = MagicMock()
        second.usage = MagicMock(
            input_tokens=130,
            output_tokens=8,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )
        total = AnthropicAdapter._usage_from_response(
            first
        ) + AnthropicAdapter._usage_from_response(second)
        assert total == TurnUsage(
            input_tokens=230,
            output_tokens=28,
            cache_read_tokens=5,
            cache_write_tokens=0,
        )

    @pytest.mark.asyncio
    async def test_emits_usage_event_when_enabled(self, mock_tools):
        """With Emit.USAGE on, a non-empty TurnUsage rides a task event's metadata."""
        from band.core.types import USAGE_EVENT_TYPE, USAGE_METADATA_KEY

        adapter = AnthropicAdapter(features=AdapterFeatures(emit={Emit.USAGE}))

        await adapter.emit_usage(
            mock_tools, TurnUsage(input_tokens=100, output_tokens=20)
        )

        mock_tools.send_event.assert_awaited_once()
        _, kwargs = mock_tools.send_event.call_args
        assert kwargs["message_type"] == USAGE_EVENT_TYPE
        payload = kwargs["metadata"][USAGE_METADATA_KEY]
        assert payload["input_tokens"] == 100
        assert payload["output_tokens"] == 20

    @pytest.mark.asyncio
    async def test_does_not_emit_usage_when_feature_off(self, mock_tools):
        """Without Emit.USAGE, emit_usage is a no-op (no event)."""
        adapter = AnthropicAdapter()  # no emit features
        await adapter.emit_usage(
            mock_tools, TurnUsage(input_tokens=100, output_tokens=20)
        )
        mock_tools.send_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_does_not_emit_empty_usage(self, mock_tools):
        """An all-zero TurnUsage is skipped even with the feature on (no false zero)."""
        adapter = AnthropicAdapter(features=AdapterFeatures(emit={Emit.USAGE}))
        await adapter.emit_usage(mock_tools, TurnUsage())
        mock_tools.send_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_usage_emit_failure_does_not_crash(self, mock_tools):
        """A send_event failure during usage emit is swallowed (best-effort)."""
        adapter = AnthropicAdapter(features=AdapterFeatures(emit={Emit.USAGE}))
        mock_tools.send_event.side_effect = Exception("403 Forbidden")
        # Should not raise.
        await adapter.emit_usage(
            mock_tools, TurnUsage(input_tokens=100, output_tokens=20)
        )

    @pytest.mark.asyncio
    async def test_emits_summed_usage_across_tool_loop(
        self, sample_message, mock_tools
    ):
        """A multi-call tool loop emits ONE usage event carrying the SUM.

        The turn makes two model calls (a tool_use round then a final answer);
        the emitted usage must be call1 + call2, proving the adapter accumulates
        across the loop rather than reporting only the first or last call. This
        is the deterministic summing proof the live smoke can't give (it never
        sees the per-call intermediates).
        """
        from types import SimpleNamespace

        from anthropic.types import TextBlock, ToolUseBlock

        from band.core.types import USAGE_EVENT_TYPE, USAGE_METADATA_KEY

        adapter = AnthropicAdapter(features=AdapterFeatures(emit={Emit.USAGE}))

        def _usage(inp: int, out: int) -> SimpleNamespace:
            return SimpleNamespace(
                input_tokens=inp,
                output_tokens=out,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            )

        # Call 1: a tool_use round (continues the loop). Call 2: the final answer.
        resp1 = SimpleNamespace(
            stop_reason="tool_use",
            content=[
                ToolUseBlock(
                    type="tool_use",
                    id="tool-1",
                    name="band_send_message",
                    input={"content": "hi"},
                )
            ],
            usage=_usage(100, 20),
        )
        resp2 = SimpleNamespace(
            stop_reason="end_turn",
            content=[TextBlock(type="text", text="Hello!")],
            usage=_usage(130, 8),
        )

        mock_tools.execute_tool_call.return_value = {"status": "success"}
        call_anthropic = AsyncMock(side_effect=[resp1, resp2])
        with patch.object(adapter, "_call_anthropic", new=call_anthropic):
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

        # Exactly two model calls were made (the loop ran twice).
        assert call_anthropic.call_count == 2
        # Find the single usage event and assert it carries the SUM (230/28),
        # not just the first (100/20) or last (130/8) call.
        usage_payloads = [
            call.kwargs["metadata"][USAGE_METADATA_KEY]
            for call in mock_tools.send_event.await_args_list
            if call.kwargs.get("message_type") == USAGE_EVENT_TYPE
            and USAGE_METADATA_KEY in (call.kwargs.get("metadata") or {})
        ]
        assert usage_payloads == [
            {
                "input_tokens": 230,
                "output_tokens": 28,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
            }
        ], f"expected one summed usage event, got {usage_payloads}"

    @pytest.mark.asyncio
    async def test_handles_tool_error(self, mock_tools):
        """Should handle tool execution errors gracefully."""
        from anthropic.types import ToolUseBlock

        adapter = AnthropicAdapter()

        mock_response = MagicMock()
        mock_response.content = [
            ToolUseBlock(
                type="tool_use",
                id="tool-1",
                name="failing_tool",
                input={},
            )
        ]

        mock_tools.execute_tool_call.side_effect = Exception("Tool failed!")

        results = await adapter._process_tool_calls(mock_response, mock_tools)

        assert len(results) == 1
        assert results[0]["is_error"] is True
        assert "Tool failed!" in results[0]["content"]


class TestErrorHandling:
    """Tests for error handling."""

    @pytest.mark.asyncio
    async def test_reports_error_on_api_failure(self, sample_message, mock_tools):
        """Should report error when Anthropic API fails."""
        adapter = AnthropicAdapter()
        await adapter.on_started("TestBot", "Test bot")

        with patch.object(adapter, "_call_anthropic") as mock_call:
            mock_call.side_effect = Exception("API Error")

            with pytest.raises(Exception, match="API Error"):
                await adapter.on_message(
                    msg=sample_message,
                    tools=mock_tools,
                    history=[],
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=True,
                    room_id="room-123",
                )

            # Should have tried to report error
            mock_tools.send_event.assert_called()


class EchoInput(BaseModel):
    """Echo back the provided message."""

    message: str = Field(description="Message to echo")


class CalculatorInput(BaseModel):
    """Perform math calculations."""

    operation: str = Field(description="add, subtract, multiply, divide")
    left: float
    right: float


async def echo_message(args: EchoInput) -> str:
    """Async echo tool."""
    return f"Echo: {args.message}"


def calculate(args: CalculatorInput) -> str:
    """Sync calculator tool."""
    ops = {
        "add": lambda a, b: a + b,
        "subtract": lambda a, b: a - b,
        "multiply": lambda a, b: a * b,
        "divide": lambda a, b: a / b,
    }
    return str(ops[args.operation](args.left, args.right))


async def failing_tool(args: EchoInput) -> str:
    """Tool that always fails."""
    raise ValueError("Service unavailable")


class TestCustomTools:
    """Tests for custom tool support."""

    def test_accepts_additional_tools_parameter(self):
        """Adapter should accept list of (Model, func) tuples."""
        adapter = AnthropicAdapter(
            additional_tools=[(EchoInput, echo_message)],
        )

        assert len(adapter._custom_tools) == 1
        assert adapter._custom_tools[0][0] is EchoInput

    def test_accepts_multiple_custom_tools(self):
        """Adapter should accept multiple custom tools."""
        adapter = AnthropicAdapter(
            additional_tools=[
                (EchoInput, echo_message),
                (CalculatorInput, calculate),
            ],
        )

        assert len(adapter._custom_tools) == 2

    @pytest.mark.asyncio
    async def test_merges_custom_tool_schemas(self, sample_message, mock_tools):
        """Custom tools should appear in schema list alongside platform tools."""
        adapter = AnthropicAdapter(
            additional_tools=[(EchoInput, echo_message)],
        )
        await adapter.on_started("TestBot", "Test bot")

        # Mock platform tools returning some schemas
        mock_tools.get_anthropic_tool_schemas = MagicMock(
            return_value=[
                {"name": "band_send_message", "description": "Send a message"}
            ]
        )

        captured_tools = []

        with patch.object(adapter, "_call_anthropic") as mock_call:
            # Capture the tools parameter
            async def capture_call(messages, tools):
                captured_tools.extend(tools)
                mock_response = MagicMock()
                mock_response.stop_reason = "end_turn"
                mock_response.content = []
                return mock_response

            mock_call.side_effect = capture_call

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

        # Should have both platform and custom tool
        assert len(captured_tools) == 2
        tool_names = [t["name"] for t in captured_tools]
        assert "band_send_message" in tool_names
        assert "echo" in tool_names

    @pytest.mark.asyncio
    async def test_routes_to_custom_tool(self, mock_tools):
        """Tool call for custom tool should execute custom function."""
        from anthropic.types import ToolUseBlock

        adapter = AnthropicAdapter(
            additional_tools=[(EchoInput, echo_message)],
        )

        mock_response = MagicMock()
        mock_response.content = [
            ToolUseBlock(
                type="tool_use",
                id="tool-1",
                name="echo",
                input={"message": "Hello world"},
            )
        ]

        results = await adapter._process_tool_calls(mock_response, mock_tools)

        # Should NOT have called platform execute_tool_call
        mock_tools.execute_tool_call.assert_not_called()

        # Should have result from custom tool
        assert len(results) == 1
        assert results[0]["is_error"] is False
        assert "Echo: Hello world" in results[0]["content"]

    @pytest.mark.asyncio
    async def test_routes_to_platform_tool(self, mock_tools):
        """Tool call for platform tool should use execute_tool_call."""
        from anthropic.types import ToolUseBlock

        adapter = AnthropicAdapter(
            additional_tools=[(EchoInput, echo_message)],
        )

        mock_response = MagicMock()
        mock_response.content = [
            ToolUseBlock(
                type="tool_use",
                id="tool-1",
                name="band_send_message",
                input={"content": "Hello", "mentions": ["User"]},
            )
        ]

        mock_tools.execute_tool_call.return_value = {"status": "sent"}

        results = await adapter._process_tool_calls(mock_response, mock_tools)

        # Should have called platform execute_tool_call
        mock_tools.execute_tool_call.assert_called_once_with(
            "band_send_message", {"content": "Hello", "mentions": ["User"]}
        )

        assert len(results) == 1
        assert results[0]["is_error"] is False

    @pytest.mark.asyncio
    async def test_custom_tool_error_sets_is_error(self, mock_tools):
        """Custom tool exception should result in is_error=True."""
        from anthropic.types import ToolUseBlock

        adapter = AnthropicAdapter(
            additional_tools=[(EchoInput, failing_tool)],
        )

        mock_response = MagicMock()
        mock_response.content = [
            ToolUseBlock(
                type="tool_use",
                id="tool-1",
                name="echo",
                input={"message": "test"},
            )
        ]

        results = await adapter._process_tool_calls(mock_response, mock_tools)

        assert len(results) == 1
        assert results[0]["is_error"] is True
        assert "Service unavailable" in results[0]["content"]

    @pytest.mark.asyncio
    async def test_preserves_tool_use_id_on_error(self, mock_tools):
        """tool_use_id should be preserved even when custom tool fails."""
        from anthropic.types import ToolUseBlock

        adapter = AnthropicAdapter(
            additional_tools=[(EchoInput, failing_tool)],
        )

        mock_response = MagicMock()
        mock_response.content = [
            ToolUseBlock(
                type="tool_use",
                id="tool-abc-123",
                name="echo",
                input={"message": "test"},
            )
        ]

        results = await adapter._process_tool_calls(mock_response, mock_tools)

        assert results[0]["tool_use_id"] == "tool-abc-123"

    @pytest.mark.asyncio
    async def test_multiple_custom_tools_execution(self, mock_tools):
        """Multiple custom tools should be callable."""
        from anthropic.types import ToolUseBlock

        adapter = AnthropicAdapter(
            additional_tools=[
                (EchoInput, echo_message),
                (CalculatorInput, calculate),
            ],
        )

        mock_response = MagicMock()
        mock_response.content = [
            ToolUseBlock(
                type="tool_use",
                id="tool-1",
                name="echo",
                input={"message": "Hello"},
            ),
            ToolUseBlock(
                type="tool_use",
                id="tool-2",
                name="calculator",
                input={"operation": "add", "left": 5, "right": 3},
            ),
        ]

        results = await adapter._process_tool_calls(mock_response, mock_tools)

        assert len(results) == 2
        assert "Echo: Hello" in results[0]["content"]
        assert "8.0" in results[1]["content"]

    @pytest.mark.asyncio
    async def test_custom_tool_validation_error(self, mock_tools):
        """Invalid args should result in validation error."""
        from anthropic.types import ToolUseBlock

        adapter = AnthropicAdapter(
            additional_tools=[(EchoInput, echo_message)],
        )

        mock_response = MagicMock()
        mock_response.content = [
            ToolUseBlock(
                type="tool_use",
                id="tool-1",
                name="echo",
                input={},  # Missing required 'message' field
            )
        ]

        results = await adapter._process_tool_calls(mock_response, mock_tools)

        assert len(results) == 1
        assert results[0]["is_error"] is True
        assert (
            "message" in results[0]["content"].lower()
        )  # Error mentions missing field
