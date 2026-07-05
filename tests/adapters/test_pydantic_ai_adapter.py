"""Tests for PydanticAIAdapter.

Tests for shared adapter behavior (initialization defaults, custom kwargs,
history_converter, on_message callable, cleanup safety) live in
tests/framework_conformance/test_adapter_conformance.py.
This file contains PydanticAI-specific behavior: agent creation, tool registration,
stream event handling, execution reporting, and custom tools.
"""

from datetime import datetime, timezone
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic_ai import (
    AgentRunResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    UnexpectedModelBehavior,
)
from pydantic_ai.messages import (
    BuiltinToolCallPart,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from band.adapters.pydantic_ai import (
    PydanticAIAdapter,
    _drop_non_replayable_messages,
    _is_replayable_history_message,
)
from band.core.types import AdapterFeatures, Capability, PlatformMessage


def make_stream_events(
    result_messages: list | None = None,
    tool_calls: list[tuple[str, dict, str]] | None = None,
    tool_results: list[tuple[str, str, str]] | None = None,
) -> AsyncIterator:
    """Create a mock async iterator of stream events.

    Args:
        result_messages: Messages to return in AgentRunResultEvent
        tool_calls: List of (tool_name, args, tool_call_id) tuples
        tool_results: List of (tool_name, output, tool_call_id) tuples

    Returns:
        Async iterator of stream events
    """

    async def stream():
        # Emit tool call events
        if tool_calls:
            for tool_name, args, tool_call_id in tool_calls:
                event = MagicMock(spec=FunctionToolCallEvent)
                event.part = MagicMock()
                event.part.tool_name = tool_name
                event.part.args = args
                event.part.tool_call_id = tool_call_id
                yield event

        # Emit tool result events
        if tool_results:
            for tool_name, output, tool_call_id in tool_results:
                event = MagicMock(spec=FunctionToolResultEvent)
                event.result = MagicMock()
                event.result.tool_name = tool_name  # tool_name is on result, not event
                event.result.content = output
                event.tool_call_id = tool_call_id
                yield event

        # Always emit final result event
        result_event = MagicMock(spec=AgentRunResultEvent)
        result_event.result = MagicMock()
        result_event.result.all_messages.return_value = result_messages or []
        yield result_event

    return stream()


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
    tools.send_message = AsyncMock(return_value={"status": "sent"})
    tools.send_event = AsyncMock(return_value={"status": "sent"})
    tools.add_participant = AsyncMock(return_value={"id": "user-1"})
    tools.remove_participant = AsyncMock(return_value={"status": "removed"})
    tools.lookup_peers = AsyncMock(return_value={"peers": []})
    tools.get_participants = AsyncMock(return_value=[])
    tools.create_chatroom = AsyncMock(return_value="new-room-123")
    return tools


@pytest.fixture
def mock_pydantic_agent():
    """Create a mock Pydantic AI Agent."""
    agent = MagicMock()
    agent._function_tools = {
        "band_send_message": MagicMock(name="band_send_message"),
        "band_send_event": MagicMock(name="band_send_event"),
        "band_add_participant": MagicMock(name="band_add_participant"),
        "band_remove_participant": MagicMock(name="band_remove_participant"),
        "band_lookup_peers": MagicMock(name="band_lookup_peers"),
        "band_get_participants": MagicMock(name="band_get_participants"),
        "band_create_chatroom": MagicMock(name="band_create_chatroom"),
    }
    return agent


class TestUsageMapping:
    """Tests for the Emit.USAGE seam's usage mapping."""

    def test_usage_from_result_current_field_names(self):
        """Maps RunUsage.input_tokens/output_tokens to TurnUsage."""
        from types import SimpleNamespace

        from band.core.types import TurnUsage

        result = MagicMock()
        result.usage.return_value = SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=5,
            cache_write_tokens=0,
        )
        assert PydanticAIAdapter._usage_from_result(result) == TurnUsage(
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=5,
            cache_write_tokens=0,
        )

    def test_usage_from_result_legacy_field_names(self):
        """Falls back to the older request_tokens/response_tokens names."""
        from types import SimpleNamespace

        from band.core.types import TurnUsage

        result = MagicMock()
        result.usage.return_value = SimpleNamespace(
            request_tokens=130,
            response_tokens=8,
        )
        assert PydanticAIAdapter._usage_from_result(result) == TurnUsage(
            input_tokens=130, output_tokens=8
        )

    def test_usage_from_result_swallows_errors(self):
        """A usage() that raises yields empty usage, never propagates."""
        from band.core.types import TurnUsage

        result = MagicMock()
        result.usage.side_effect = RuntimeError("no usage")
        assert PydanticAIAdapter._usage_from_result(result) == TurnUsage()

    def test_usage_from_messages_sums_model_responses(self):
        """The benign-path fallback sums usage across captured ModelResponses.

        Covers the empty-final-response path (no AgentRunResultEvent fires) where
        the turn still spent tokens — each ModelResponse carries its own usage.
        """
        from types import SimpleNamespace

        from pydantic_ai.messages import ModelRequest, ModelResponse

        from band.core.types import TurnUsage

        def response(inp, out):
            r = ModelResponse.__new__(ModelResponse)
            object.__setattr__(
                r, "usage", SimpleNamespace(input_tokens=inp, output_tokens=out)
            )
            return r

        messages = [
            ModelRequest(parts=[]),  # non-response: ignored
            response(100, 20),
            response(130, 8),
        ]
        assert PydanticAIAdapter._usage_from_messages(messages) == TurnUsage(
            input_tokens=230, output_tokens=28
        )

    def test_usage_from_messages_empty_when_no_responses(self):
        """No ModelResponse in the captured messages → empty usage."""
        from pydantic_ai.messages import ModelRequest

        from band.core.types import TurnUsage

        assert (
            PydanticAIAdapter._usage_from_messages([ModelRequest(parts=[])])
            == TurnUsage()
        )

    def test_new_run_messages_isolates_this_run_despite_history_merge(self):
        """Identity (not position) isolates this run when pydantic-ai merges history.

        Regression guard: pydantic-ai's ``_clean_message_history`` merges adjacent
        same-type messages in the passed history (e.g. the injected participants +
        contacts requests), so ``captured`` is *shorter* than the raw prior history
        and a ``len(prior)`` slice would drop this turn's response. Real API
        responses keep their identity, so the identity filter still isolates this
        run — and combined with the ModelResponse-only sum, yields only this turn's
        usage.
        """
        from types import SimpleNamespace

        from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart

        from band.core.types import TurnUsage

        def response(inp, out):
            r = ModelResponse.__new__(ModelResponse)
            object.__setattr__(
                r, "usage", SimpleNamespace(input_tokens=inp, output_tokens=out)
            )
            return r

        # Prior history: a real response, then two instruction-less requests that
        # pydantic-ai would merge into one on the next run.
        prior_resp = response(100, 20)
        req_participants = ModelRequest(parts=[UserPromptPart(content="[System]: p")])
        req_contacts = ModelRequest(parts=[UserPromptPart(content="[System]: c")])
        prior = [prior_resp, req_participants, req_contacts]
        prior_ids = {id(m) for m in prior}

        # captured after cleaning: prior_resp survives by identity, the two
        # requests are merged into ONE new object, then this run's new response is
        # appended. So len(captured)=3 < len(prior)=3+... a positional
        # captured[len(prior):] would slice to empty and drop new_resp.
        merged_req = ModelRequest(parts=[UserPromptPart(content="[System]: p\nc")])
        new_resp = response(130, 8)
        captured = [prior_resp, merged_req, new_resp]

        this_run = PydanticAIAdapter._new_run_messages(captured, prior_ids)
        # The merged request (new identity) rides along but carries no usage; only
        # this run's response contributes.
        assert PydanticAIAdapter._usage_from_messages(this_run) == TurnUsage(
            input_tokens=130, output_tokens=8
        )


class TestInitialization:
    """Tests for adapter initialization."""

    def test_requires_model(self):
        """Should require model parameter."""
        # model is required - no default
        adapter = PydanticAIAdapter(model="openai:gpt-5.4")
        assert adapter.model == "openai:gpt-5.4"

    def test_create_agent_uses_str_output_type(self):
        """INT-488: Agent must be constructed with output_type=str, never None.

        pydantic-ai-slim 1.87+ raises UserError("At least one output type must
        be provided other than `None`") when output_type is None or omitted.
        """
        adapter = PydanticAIAdapter(model="openai:gpt-5.4")
        adapter.agent_name = "TestBot"

        with patch("band.adapters.pydantic_ai.Agent") as MockAgent:
            adapter._create_agent()
            assert MockAgent.call_args.kwargs["output_type"] is str

    def test_create_agent_registers_content_null_history_processor(self):
        """The agent must sanitize content:null responses on every request.

        Registering the drop as a history processor (not just the post-run
        storage filter) is what closes the mid-run gap: the model can emit an
        empty/thinking-only response within a single turn, and pydantic-ai would
        otherwise replay it to the provider as assistant content:null.
        """
        adapter = PydanticAIAdapter(model="openai:gpt-5.4")
        adapter.agent_name = "TestBot"

        with patch("band.adapters.pydantic_ai.Agent") as MockAgent:
            adapter._create_agent()
            assert MockAgent.call_args.kwargs["history_processors"] == [
                _drop_non_replayable_messages
            ]


class TestOnStarted:
    """Tests for on_started() method."""

    @pytest.mark.asyncio
    async def test_sets_agent_name_and_description(self, mock_pydantic_agent):
        """Should set agent_name and agent_description."""
        adapter = PydanticAIAdapter(model="openai:gpt-5.4")

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

        assert adapter.agent_name == "TestBot"
        assert adapter.agent_description == "A test bot"

    @pytest.mark.asyncio
    async def test_creates_pydantic_agent(self, mock_pydantic_agent):
        """Should create Pydantic AI agent after start."""
        adapter = PydanticAIAdapter(model="openai:gpt-5.4")

        assert adapter._agent is None

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

        assert adapter._agent is not None

    @pytest.mark.asyncio
    async def test_persists_rendered_system_prompt(self):
        """Should persist rendered prompt for capability-gating visibility."""
        with patch("band.adapters.pydantic_ai.Agent"):
            adapter = PydanticAIAdapter(
                model="openai:gpt-5.4",
                features=AdapterFeatures(capabilities={Capability.MEMORY}),
            )
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

        assert adapter._system_prompt is not None
        assert "Memory Tools" in adapter._system_prompt

    @pytest.mark.asyncio
    async def test_agent_has_tools_registered(self, mock_pydantic_agent):
        """Should register all platform tools on the agent."""
        adapter = PydanticAIAdapter(model="openai:gpt-5.4")

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

        # Get registered tool names
        tool_names = list(adapter._agent._function_tools.keys())

        expected_tools = [
            "band_send_message",
            "band_send_event",
            "band_add_participant",
            "band_remove_participant",
            "band_lookup_peers",
            "band_get_participants",
            "band_create_chatroom",
        ]

        for tool in expected_tools:
            assert tool in tool_names, f"Tool {tool} not found"


class TestOnMessage:
    """Tests for on_message() method."""

    @pytest.mark.asyncio
    async def test_initializes_history_on_bootstrap(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """Should initialize room history on first message."""
        adapter = PydanticAIAdapter(model="openai:gpt-5.4")

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        result_messages = [ModelRequest(parts=[UserPromptPart(content="test")])]
        adapter._agent.run_stream_events = MagicMock(
            return_value=make_stream_events(result_messages=result_messages)
        )

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

    @pytest.mark.asyncio
    async def test_loads_existing_history(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """Should load historical messages on bootstrap."""
        adapter = PydanticAIAdapter(model="openai:gpt-5.4")

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        existing_history = [
            ModelRequest(parts=[UserPromptPart(content="[Bob]: Previous message")]),
            ModelResponse(parts=[TextPart(content="Previous response")]),
        ]

        result_messages = existing_history + [
            ModelRequest(parts=[UserPromptPart(content="new")])
        ]
        adapter._agent.run_stream_events = MagicMock(
            return_value=make_stream_events(result_messages=result_messages)
        )

        await adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=existing_history,
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        # Verify history was passed to agent.run_stream_events()
        call_kwargs = adapter._agent.run_stream_events.call_args.kwargs
        assert "message_history" in call_kwargs
        assert len(call_kwargs["message_history"]) == 2

    @pytest.mark.asyncio
    async def test_injects_participants_message(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """Should inject participants update when provided."""
        adapter = PydanticAIAdapter(model="openai:gpt-5.4")

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        adapter._agent.run_stream_events = MagicMock(
            return_value=make_stream_events(result_messages=[])
        )

        await adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=[],
            participants_msg="Alice joined the room",
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        # Check that participant message was added to history before run
        call_kwargs = adapter._agent.run_stream_events.call_args.kwargs
        message_history = call_kwargs.get("message_history", [])
        # First message should be the participant update
        if message_history:
            first_msg = message_history[0]
            assert isinstance(first_msg, ModelRequest)
            assert "[System]: Alice joined" in first_msg.parts[0].content

    @pytest.mark.asyncio
    async def test_creates_agent_lazily_if_not_started(
        self, sample_message, mock_tools
    ):
        """Should create agent lazily if on_started wasn't called."""
        adapter = PydanticAIAdapter(
            model="openai:gpt-5.4",
            custom_section="Test section",
        )
        # Don't call on_started - set agent_name directly for prompt rendering
        adapter.agent_name = "LazyBot"

        with patch.object(adapter, "_create_agent") as mock_create:
            mock_agent = MagicMock()
            mock_agent.run_stream_events = MagicMock(
                return_value=make_stream_events(result_messages=[])
            )
            mock_create.return_value = mock_agent

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            mock_create.assert_called_once()


class TestOnCleanup:
    """Tests for on_cleanup() method."""

    @pytest.mark.asyncio
    async def test_cleans_up_room_history(self):
        """Should remove room history on cleanup."""
        adapter = PydanticAIAdapter(model="openai:gpt-5.4")

        # Add some history
        adapter._message_history["room-123"] = [
            ModelRequest(parts=[UserPromptPart(content="test")])
        ]
        assert "room-123" in adapter._message_history

        await adapter.on_cleanup("room-123")

        assert "room-123" not in adapter._message_history


class TestHistoryManagement:
    """Tests for message history management."""

    @pytest.mark.asyncio
    async def test_updates_history_after_run(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """Should update stored history with all messages from run."""
        adapter = PydanticAIAdapter(model="openai:gpt-5.4")

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        new_messages = [
            ModelRequest(parts=[UserPromptPart(content="Q1")]),
            ModelResponse(parts=[TextPart(content="A1")]),
        ]

        adapter._agent.run_stream_events = MagicMock(
            return_value=make_stream_events(result_messages=new_messages)
        )

        await adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        assert adapter._message_history["room-123"] == new_messages

    @pytest.mark.asyncio
    async def test_keeps_native_history_and_drops_content_null_responses(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """Should keep native tool history but drop responses that replay as null."""
        adapter = PydanticAIAdapter(model="openai:gpt-5.4")

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        user_request = ModelRequest(parts=[UserPromptPart(content="Q1")])
        tool_call_response = ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="band_send_message",
                    args={"content": "A1", "mentions": ["Alice"]},
                    tool_call_id="call_1",
                )
            ]
        )
        tool_return_request = ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="band_send_message",
                    content={"id": "msg_1"},
                    tool_call_id="call_1",
                )
            ]
        )
        content_null_response = ModelResponse(parts=[])
        text_response = ModelResponse(parts=[TextPart(content="A1")])
        result_messages = [
            user_request,
            tool_call_response,
            tool_return_request,
            content_null_response,
            text_response,
        ]
        adapter._agent.run_stream_events = MagicMock(
            return_value=make_stream_events(result_messages=result_messages)
        )

        await adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        stored_history = adapter._message_history["room-123"]
        assert stored_history == [
            user_request,
            tool_call_response,
            tool_return_request,
            text_response,
        ]
        assert content_null_response not in stored_history

    def test_keeps_response_with_only_builtin_tool_part(self):
        """Builtin tool calls carry content the provider expects — keep them."""
        response = ModelResponse(
            parts=[
                BuiltinToolCallPart(
                    tool_name="web_search",
                    args={"query": "weather"},
                    tool_call_id="call_1",
                )
            ]
        )

        assert _is_replayable_history_message(response) is True

    def test_history_processor_strips_content_null_responses(self):
        """The processor drops empty/thinking-only responses, keeps real content.

        This runs before every model request (mid-run included), so an empty or
        thinking-only response the model emits within a turn is never replayed as
        assistant content:null — which providers reject.
        """
        user_request = ModelRequest(parts=[UserPromptPart(content="Q1")])
        tool_call = ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="band_send_message",
                    args={"content": "hi", "mentions": ["Alice"]},
                    tool_call_id="call_1",
                )
            ]
        )
        tool_return = ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="band_send_message",
                    content={"id": "msg_1"},
                    tool_call_id="call_1",
                )
            ]
        )
        empty_response = ModelResponse(parts=[])
        thinking_only = ModelResponse(parts=[ThinkingPart(content="hmm")])
        text_response = ModelResponse(parts=[TextPart(content="done")])

        processed = _drop_non_replayable_messages(
            [
                user_request,
                tool_call,
                tool_return,
                empty_response,
                thinking_only,
                text_response,
            ]
        )

        assert processed == [user_request, tool_call, tool_return, text_response]
        assert empty_response not in processed
        assert thinking_only not in processed

    @pytest.mark.asyncio
    async def test_ensures_history_exists_for_non_bootstrap(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """Should create history if not bootstrap and room doesn't exist."""
        adapter = PydanticAIAdapter(model="openai:gpt-5.4")

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        adapter._agent.run_stream_events = MagicMock(
            return_value=make_stream_events(result_messages=[])
        )

        await adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,  # Not bootstrap
            room_id="new-room",
        )

        # Should have created empty history
        assert "new-room" in adapter._message_history


class TestExecutionReporting:
    """Tests for execution reporting (tool_call and tool_result events)."""

    @pytest.mark.asyncio
    async def test_emits_tool_call_events_when_enabled(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """Should emit tool_call events when enable_execution_reporting=True."""
        adapter = PydanticAIAdapter(
            model="openai:gpt-5.4",
            enable_execution_reporting=True,
        )

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        adapter._agent.run_stream_events = MagicMock(
            return_value=make_stream_events(
                result_messages=[],
                tool_calls=[("band_send_message", {"content": "Hello"}, "call-123")],
            )
        )

        await adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        # Verify send_event was called with tool_call
        mock_tools.send_event.assert_any_call(
            content='{"name": "band_send_message", "args": {"content": "Hello"}, "tool_call_id": "call-123"}',
            message_type="tool_call",
        )

    @pytest.mark.asyncio
    async def test_emits_tool_result_events_when_enabled(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """Should emit tool_result events when enable_execution_reporting=True."""
        adapter = PydanticAIAdapter(
            model="openai:gpt-5.4",
            enable_execution_reporting=True,
        )

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        adapter._agent.run_stream_events = MagicMock(
            return_value=make_stream_events(
                result_messages=[],
                tool_results=[
                    ("band_send_message", "Message sent successfully", "call-123")
                ],
            )
        )

        await adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        # Verify send_event was called with tool_result
        mock_tools.send_event.assert_any_call(
            content='{"name": "band_send_message", "output": "Message sent successfully", "tool_call_id": "call-123"}',
            message_type="tool_result",
        )

    @pytest.mark.asyncio
    async def test_no_events_when_reporting_disabled(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """Should NOT emit events when enable_execution_reporting=False (default)."""
        adapter = PydanticAIAdapter(model="openai:gpt-5.4")  # Default is False

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        adapter._agent.run_stream_events = MagicMock(
            return_value=make_stream_events(
                result_messages=[],
                tool_calls=[("band_send_message", {"content": "Hello"}, "call-123")],
                tool_results=[("band_send_message", "Message sent", "call-123")],
            )
        )

        await adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        # Verify send_event was NOT called for tool_call or tool_result
        for call in mock_tools.send_event.call_args_list:
            _, kwargs = call
            assert kwargs.get("message_type") not in ["tool_call", "tool_result"]

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_all_reported(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """Should emit events for all tool calls in sequence."""
        adapter = PydanticAIAdapter(
            model="openai:gpt-5.4",
            enable_execution_reporting=True,
        )

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        adapter._agent.run_stream_events = MagicMock(
            return_value=make_stream_events(
                result_messages=[],
                tool_calls=[
                    ("band_lookup_peers", {}, "call-1"),
                    ("band_add_participant", {"identifier": "Helper"}, "call-2"),
                    ("band_send_message", {"content": "Done"}, "call-3"),
                ],
                tool_results=[
                    ("band_lookup_peers", "[{...}]", "call-1"),
                    ("band_add_participant", "Added", "call-2"),
                    ("band_send_message", "Sent", "call-3"),
                ],
            )
        )

        await adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        # Count tool_call and tool_result events
        tool_call_count = sum(
            1
            for call in mock_tools.send_event.call_args_list
            if call.kwargs.get("message_type") == "tool_call"
        )
        tool_result_count = sum(
            1
            for call in mock_tools.send_event.call_args_list
            if call.kwargs.get("message_type") == "tool_result"
        )

        assert tool_call_count == 3
        assert tool_result_count == 3

    @pytest.mark.asyncio
    async def test_event_failure_does_not_crash_run(
        self, sample_message, mock_pydantic_agent
    ):
        """Should continue running if send_event fails."""
        adapter = PydanticAIAdapter(
            model="openai:gpt-5.4",
            enable_execution_reporting=True,
        )

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        # Mock tools where send_event fails with a real transport error (the kind
        # _report_error narrowly tolerates); a generic Exception would be a bug and
        # is intentionally left to propagate.
        failing_tools = AsyncMock()
        failing_tools.send_event = AsyncMock(
            side_effect=httpx.ConnectError("Network error")
        )

        adapter._agent.run_stream_events = MagicMock(
            return_value=make_stream_events(
                result_messages=[ModelRequest(parts=[UserPromptPart(content="test")])],
                tool_calls=[("band_send_message", {"content": "Hello"}, "call-123")],
            )
        )

        # Should not raise
        await adapter.on_message(
            msg=sample_message,
            tools=failing_tools,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        # History should still be updated
        assert "room-123" in adapter._message_history


def make_raising_stream(
    error: BaseException,
    *,
    tool_result: bool,
    tool_name: str = "band_send_message",
    tool_content: Any = None,
) -> AsyncIterator:
    """Async run stream that optionally fires a tool-result event, then raises.

    ``tool_name``/``tool_content`` let a test pick a read-only tool or an error
    result to verify those do not count as terminal productive work.
    """

    async def stream():
        if tool_result:
            event = MagicMock(spec=FunctionToolResultEvent)
            event.result = MagicMock()
            event.result.tool_name = tool_name
            event.result.content = (
                {"id": "msg_1"} if tool_content is None else tool_content
            )
            event.tool_call_id = "call_1"
            yield event
        raise error

    return stream()


class TestEmptyFinalAnswer:
    """gpt-5.4-mini can return an empty final answer after the agent already
    replied/acted via tools, exhausting pydantic-ai's output_type=str validation
    retries. That is benign — the work already went out — so it must not fail the
    message, but a genuine no-work failure must still surface.
    """

    @pytest.mark.asyncio
    async def test_empty_output_after_tool_is_benign(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """Output-validation exhaustion after a tool ran is swallowed."""
        adapter = PydanticAIAdapter(model="openai:gpt-5.4")
        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        adapter._agent.run_stream_events = MagicMock(
            return_value=make_raising_stream(
                UnexpectedModelBehavior(
                    "Exceeded maximum retries (1) for output validation"
                ),
                tool_result=True,
            )
        )

        # Must not raise: the reply already went out via the tool this turn.
        await adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        # Regression (fallback path): with the run mocked, capture_run_messages records
        # nothing, so the swallow falls back to preserving at least the user prompt so
        # the next same-session turn isn't amnesiac.
        from pydantic_ai.messages import ModelRequest, UserPromptPart

        preserved = adapter._message_history["room-123"]
        assert preserved, "swallowed turn should still record the user message"
        assert isinstance(preserved[-1], ModelRequest)
        assert any(
            isinstance(part, UserPromptPart) and "Hello, agent!" in str(part.content)
            for part in preserved[-1].parts
        )

    @pytest.mark.asyncio
    async def test_empty_output_preserves_full_captured_turn(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """The swallow persists the whole captured turn — not just the user prompt —
        so a later 'what did you just say?' has the agent's reply in context."""
        from contextlib import contextmanager

        from pydantic_ai.messages import (
            ModelRequest,
            ModelResponse,
            TextPart,
            UserPromptPart,
        )

        adapter = PydanticAIAdapter(model="openai:gpt-5.4")
        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        adapter._agent.run_stream_events = MagicMock(
            return_value=make_raising_stream(
                UnexpectedModelBehavior(
                    "Exceeded maximum retries (1) for output validation"
                ),
                tool_result=True,
            )
        )

        # pydantic-ai populates capture_run_messages during a real run; simulate a
        # run that captured the full turn (user prompt + the agent's response).
        full_turn = [
            ModelRequest(parts=[UserPromptPart(content="[Alice]: hi")]),
            ModelResponse(parts=[TextPart(content="replied via tool")]),
        ]

        @contextmanager
        def fake_capture():
            yield full_turn

        with patch("band.adapters.pydantic_ai.capture_run_messages", fake_capture):
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-xyz",
            )

        preserved = adapter._message_history["room-xyz"]
        # The full turn is kept — crucially the assistant response, not only the user.
        assert preserved == full_turn
        assert any(isinstance(message, ModelResponse) for message in preserved)

    @pytest.mark.asyncio
    async def test_empty_output_without_tool_propagates(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """Same error with no tool executed is a real failure — propagate."""
        adapter = PydanticAIAdapter(model="openai:gpt-5.4")
        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        adapter._agent.run_stream_events = MagicMock(
            return_value=make_raising_stream(
                UnexpectedModelBehavior(
                    "Exceeded maximum retries (1) for output validation"
                ),
                tool_result=False,
            )
        )

        with pytest.raises(UnexpectedModelBehavior):
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

    @pytest.mark.asyncio
    async def test_failed_run_still_emits_captured_usage(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """A run that raises still emits the usage its captured responses accrued.

        Tokens spent before the failure were still spent: the finally-based emit
        falls back to summing this run's captured ModelResponses when no result
        event fired, so a hard mid-run failure doesn't silently drop usage."""
        from contextlib import contextmanager
        from types import SimpleNamespace

        from band.core.types import Emit
        from tests.adapters.usage_events import sent_usage_payloads

        adapter = PydanticAIAdapter(
            model="openai:gpt-5.4",
            features=AdapterFeatures(emit={Emit.USAGE}),
        )
        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        adapter._agent.run_stream_events = MagicMock(
            return_value=make_raising_stream(
                UnexpectedModelBehavior("Received empty model response"),
                tool_result=True,
            )
        )

        # Simulate a run that captured a response with usage before raising.
        failed_response = ModelResponse.__new__(ModelResponse)
        object.__setattr__(
            failed_response,
            "usage",
            SimpleNamespace(input_tokens=100, output_tokens=20),
        )
        object.__setattr__(failed_response, "parts", [TextPart(content="partial")])
        captured_turn = [
            ModelRequest(parts=[UserPromptPart(content="[Alice]: hi")]),
            failed_response,
        ]

        @contextmanager
        def fake_capture():
            yield captured_turn

        with patch("band.adapters.pydantic_ai.capture_run_messages", fake_capture):
            with pytest.raises(UnexpectedModelBehavior):
                await adapter.on_message(
                    msg=sample_message,
                    tools=mock_tools,
                    history=[],
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=True,
                    room_id="room-123",
                )

        usage_payloads = sent_usage_payloads(mock_tools.send_event)
        assert usage_payloads == [
            {
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
            }
        ], f"expected the captured run's usage to be emitted, got {usage_payloads}"

    @pytest.mark.asyncio
    async def test_unrelated_model_error_propagates_even_after_tool(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """The swallow is narrow: other model errors still surface after a tool."""
        adapter = PydanticAIAdapter(model="openai:gpt-5.4")
        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        adapter._agent.run_stream_events = MagicMock(
            return_value=make_raising_stream(
                UnexpectedModelBehavior("Received empty model response"),
                tool_result=True,
            )
        )

        with pytest.raises(UnexpectedModelBehavior):
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

    @pytest.mark.asyncio
    async def test_empty_output_after_read_only_tool_propagates(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """A read-only lookup is not terminal work — output-validation exhaustion
        after only a lookup is a genuine no-response failure and must propagate."""
        adapter = PydanticAIAdapter(model="openai:gpt-5.4")
        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        adapter._agent.run_stream_events = MagicMock(
            return_value=make_raising_stream(
                UnexpectedModelBehavior(
                    "Exceeded maximum retries (1) for output validation"
                ),
                tool_result=True,
                tool_name="band_lookup_peers",
                tool_content=[{"id": "peer_1"}],
            )
        )

        with pytest.raises(UnexpectedModelBehavior):
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

    @pytest.mark.asyncio
    async def test_empty_output_after_failed_band_tool_propagates(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """A band tool that returned an "Error ..." string did no work — exhausting
        output validation afterward is a genuine failure and must propagate."""
        adapter = PydanticAIAdapter(model="openai:gpt-5.4")
        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        adapter._agent.run_stream_events = MagicMock(
            return_value=make_raising_stream(
                UnexpectedModelBehavior(
                    "Exceeded maximum retries (1) for output validation"
                ),
                tool_result=True,
                tool_name="band_send_message",
                tool_content="Error sending message: no mentions",
            )
        )

        with pytest.raises(UnexpectedModelBehavior):
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )


class TestCustomTools:
    """Tests for custom tool support (PydanticAI-native functions)."""

    def test_accepts_additional_tools_parameter(self):
        """Adapter accepts list of callables."""

        async def my_tool(ctx, message: str) -> str:
            """A custom tool."""
            return f"Echo: {message}"

        adapter = PydanticAIAdapter(
            model="openai:gpt-5.4",
            additional_tools=[my_tool],
        )

        assert len(adapter._custom_tools) == 1
        assert adapter._custom_tools[0] == my_tool

    def test_multiple_custom_tools(self):
        """Should accept multiple custom tools."""

        async def tool_one(ctx, a: int) -> int:
            """Tool one."""
            return a + 1

        def tool_two(ctx, b: str) -> str:
            """Tool two."""
            return b.upper()

        async def tool_three(ctx, x: float, y: float) -> float:
            """Tool three."""
            return x + y

        adapter = PydanticAIAdapter(
            model="openai:gpt-5.4",
            additional_tools=[tool_one, tool_two, tool_three],
        )

        assert len(adapter._custom_tools) == 3

    @pytest.mark.asyncio
    async def test_registers_custom_tools_with_agent(self):
        """Custom tools should be registered via agent.tool()."""

        async def my_echo(ctx, message: str) -> str:
            """Echo the message."""
            return f"Echo: {message}"

        adapter = PydanticAIAdapter(
            model="openai:gpt-5.4",
            additional_tools=[my_echo],
        )

        # Mock the Agent class to track tool registrations
        registered_tools = []

        with patch("band.adapters.pydantic_ai.Agent") as MockAgent:
            mock_agent = MagicMock()
            mock_agent.tool = MagicMock(
                side_effect=lambda f: registered_tools.append(f)
            )
            MockAgent.return_value = mock_agent

            await adapter.on_started("TestBot", "Test bot")

        # Should have registered platform tools + custom tool
        tool_names = [t.__name__ for t in registered_tools]
        assert "my_echo" in tool_names

    @pytest.mark.asyncio
    async def test_custom_tool_appears_in_agent_function_tools(
        self, mock_pydantic_agent
    ):
        """Custom tool should appear in agent._function_tools after registration."""

        async def calculator(ctx, a: float, b: float) -> float:
            """Add two numbers."""
            return a + b

        adapter = PydanticAIAdapter(
            model="openai:gpt-5.4",
            additional_tools=[calculator],
        )

        # Add calculator to mock agent's function tools when tool() is called
        def register_tool(func):
            mock_pydantic_agent._function_tools[func.__name__] = MagicMock(
                name=func.__name__
            )

        mock_pydantic_agent.tool = MagicMock(side_effect=register_tool)

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            # Manually call tool registration since we're mocking _create_agent
            for custom_tool in adapter._custom_tools:
                mock_pydantic_agent.tool(custom_tool)

        assert "calculator" in mock_pydantic_agent._function_tools

    @pytest.mark.asyncio
    async def test_custom_tools_work_with_on_message(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """Custom tools should work during message handling."""

        async def my_helper(ctx, value: str) -> str:
            """Helper tool."""
            return f"Helped: {value}"

        adapter = PydanticAIAdapter(
            model="openai:gpt-5.4",
            additional_tools=[my_helper],
        )

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        result_messages = [ModelRequest(parts=[UserPromptPart(content="test")])]
        adapter._agent.run_stream_events = MagicMock(
            return_value=make_stream_events(result_messages=result_messages)
        )

        # Should not raise
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


class TestPortableCustomToolDef:
    """pydantic accepts the portable CustomToolDef (InputModel, handler) tuple form —
    the same custom-tool shape anthropic/crewai/claude_sdk/langgraph take."""

    def test_tuple_is_normalized_to_a_named_callable(self):
        from pydantic import BaseModel

        class LookupInput(BaseModel):
            """look up a code."""

            key: str

        def lookup(args: LookupInput) -> str:
            return f"code:{args.key}"

        adapter = PydanticAIAdapter(
            model="openai:gpt-5.4", additional_tools=[(LookupInput, lookup)]
        )
        # Normalized to a native callable named from the model (not the handler).
        assert [t.__name__ for t in adapter._custom_tools] == ["lookup"]
        # ...and it still delegates to the handler.
        assert adapter._custom_tools[0](LookupInput(key="alpha")) == "code:alpha"

    def test_tuple_terminal_marker_is_honored(self):
        from pydantic import BaseModel

        class DeployInput(BaseModel):
            """deploy."""

            target: str

        def deploy(args: DeployInput) -> str:
            return "done"

        deploy.band_terminal = True  # opt in as a terminal action

        adapter = PydanticAIAdapter(
            model="openai:gpt-5.4", additional_tools=[(DeployInput, deploy)]
        )
        assert adapter._custom_terminal_names == frozenset({"deploy"})

    def test_converted_tuple_flattens_in_pydantic_ai(self):
        from pydantic import BaseModel
        from pydantic_ai import Agent
        from pydantic_ai.models.test import TestModel

        from band.adapters.pydantic_ai import _custom_tool_def_to_callable

        class LookupInput(BaseModel):
            """look up a code."""

            key: str

        def lookup(args: LookupInput) -> str:
            return f"code:{args.key}"

        native = _custom_tool_def_to_callable((LookupInput, lookup))
        agent = Agent(TestModel())
        agent.tool_plain(native)
        (tool,) = agent._function_toolset.tools.values()
        schema = tool.function_schema.json_schema
        # pydantic-ai flattens the single model param into the tool's args.
        assert tool.name == "lookup"
        assert sorted((schema.get("properties") or {}).keys()) == ["key"]
