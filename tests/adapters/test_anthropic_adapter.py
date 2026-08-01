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
from band.core.backends.oneshot import run_adapter_turn
from band.core.tools import FunctionTool
from band.core.types import AdapterFeatures, Emit, PlatformMessage, TurnUsage
from band.runtime.tools import ToolCallOutcome
from tests.adapters.usage_events import sent_usage_payloads
from tests.modelclients import (
    ScriptedAnthropicClient,
    anthropic_reply,
    anthropic_usage,
)


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
def scripted():
    """Build an adapter whose Anthropic client replays scripted replies.

    Injection at the SDK client keeps the provider's real request projection
    and response mapping inside every turn these tests drive, and the returned
    client records the payloads that reached the wire.
    """

    def build(*replies, **kwargs) -> AnthropicAdapter:
        adapter = AnthropicAdapter(**kwargs)
        adapter.client = ScriptedAnthropicClient(list(replies))
        return adapter

    return build


async def deliver(adapter, msg, tools, **overrides) -> None:
    """Deliver one platform message with the turn's usual defaults."""
    await adapter.on_message(
        msg=msg,
        tools=tools,
        history=overrides.pop("history", []),
        participants_msg=overrides.pop("participants_msg", None),
        contacts_msg=overrides.pop("contacts_msg", None),
        is_session_bootstrap=overrides.pop("is_session_bootstrap", True),
        room_id=overrides.pop("room_id", "room-123"),
        **overrides,
    )


@pytest.fixture
def mock_tools():
    """Create mock AgentToolsProtocol (MagicMock base, AsyncMock methods)."""
    tools = MagicMock()
    tools.get_tool_schemas = MagicMock(return_value=[])
    tools.send_message = AsyncMock(return_value={"status": "sent"})
    tools.send_event = AsyncMock(return_value={"status": "sent"})
    tools.execute_tool_call_structured = AsyncMock(
        return_value=ToolCallOutcome(value={"status": "success"}, ok=True)
    )
    return tools


class TestInitialization:
    """Tests for adapter initialization."""

    def test_replace_instructions(self):
        """Should preserve explicit REPLACE instructions."""
        from band.core.instructions import Instruction, InstructionMode

        adapter = AnthropicAdapter(
            instructions=Instruction(
                text="You are a custom assistant.",
                mode=InstructionMode.REPLACE,
            ),
        )

        assert adapter._instructions == Instruction(
            text="You are a custom assistant.",
            mode=InstructionMode.REPLACE,
        )


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
    async def test_uses_replace_instructions_when_provided(self):
        """Should replace rendered instructions when explicitly requested."""
        from band.core.instructions import Instruction, InstructionMode

        adapter = AnthropicAdapter(
            instructions=Instruction(
                text="Custom prompt here.",
                mode=InstructionMode.REPLACE,
            )
        )

        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")

        assert adapter._system_prompt == "Custom prompt here."


class TestOnMessage:
    """Tests for on_message() method."""

    @pytest.mark.asyncio
    async def test_initializes_history_on_bootstrap(
        self, sample_message, mock_tools, scripted
    ):
        """Should initialize room history on first message."""
        adapter = scripted(anthropic_reply("Hi there"))
        await adapter.on_started("TestBot", "Test bot")

        await deliver(adapter, sample_message, mock_tools)

        assert adapter.session_history("room-123")
        assert len(adapter.session_history("room-123")) >= 1

    @pytest.mark.asyncio
    async def test_loads_existing_history(self, sample_message, mock_tools, scripted):
        """Bootstrap history is seeded into the session and reaches the model."""
        adapter = scripted(anthropic_reply("Hi there"))
        await adapter.on_started("TestBot", "Test bot")

        existing_history = [
            {"role": "user", "content": "[Bob]: Previous message"},
            {"role": "assistant", "content": "Previous response"},
        ]

        await deliver(adapter, sample_message, mock_tools, history=existing_history)

        # Existing 2 + the current message.
        assert len(adapter.session_history("room-123")) >= 3
        sent = [m["content"] for m in adapter.client.last_payload["messages"]]
        assert "[Bob]: Previous message" in sent, (
            f"bootstrap history never reached the model: {sent}"
        )

    @pytest.mark.asyncio
    async def test_injects_participants_message(
        self, sample_message, mock_tools, scripted
    ):
        """Should inject participants update when provided."""
        adapter = scripted(anthropic_reply("Hi there"))
        await adapter.on_started("TestBot", "Test bot")

        await deliver(
            adapter,
            sample_message,
            mock_tools,
            participants_msg="Alice joined the room",
        )

        found = any(
            "[System]: Alice joined" in str(m.get("content", ""))
            for m in adapter.session_history("room-123")
        )
        assert found

    @pytest.mark.asyncio
    async def test_system_prompt_and_tools_reach_the_wire(
        self, sample_message, mock_tools, scripted
    ):
        """The rendered instructions and the room's tool schemas are what the
        provider actually sends — nothing between the adapter and the SDK drops
        them."""
        adapter = scripted(anthropic_reply("Hi there"))
        mock_tools.get_anthropic_tool_schemas = MagicMock(
            return_value=[{"name": "band_send_message", "description": "Send"}]
        )
        await adapter.on_started("TestBot", "Test bot")

        await deliver(adapter, sample_message, mock_tools)

        payload = adapter.client.last_payload
        assert payload["system"] == adapter._system_prompt
        assert [tool["name"] for tool in payload["tools"]] == ["band_send_message"]


class TestOnCleanup:
    """Tests for on_cleanup() method."""

    @pytest.mark.asyncio
    async def test_cleans_up_room_history(self, sample_message, mock_tools):
        """Should remove room history on cleanup."""
        adapter = AnthropicAdapter()
        await adapter.on_started("TestBot", "Test bot")

        # First add some history
        adapter._backend.bind_session("room-123", [])
        assert adapter._backend.has_session("room-123")

        await adapter.on_cleanup("room-123")

        assert not adapter._backend.has_session("room-123")


class TestToolExecution:
    """Tests for tool execution."""

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
    async def test_usage_emit_skipped_during_task_cancellation(self, mock_tools):
        """A cancelled turn must not fire usage I/O from its finally: teardown
        (shutdown, a turn timeout) would otherwise block on a REST call, and a
        CancelledError raised mid-send could skip later cleanup."""
        import asyncio

        adapter = AnthropicAdapter(features=AdapterFeatures(emit={Emit.USAGE}))
        started = asyncio.Event()

        async def turn() -> None:
            try:
                started.set()
                await asyncio.sleep(30)
            finally:
                await adapter.emit_usage(
                    mock_tools, TurnUsage(input_tokens=1, output_tokens=1)
                )

        task = asyncio.create_task(turn())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        mock_tools.send_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_emits_summed_usage_across_tool_loop(
        self, sample_message, mock_tools, scripted
    ):
        """A multi-call tool loop emits ONE usage event carrying the SUM.

        The turn makes two model calls (a tool_use round then a final answer);
        the emitted usage must be call1 + call2, proving the adapter accumulates
        across the loop rather than reporting only the first or last call. This
        is the deterministic summing proof the live smoke can't give (it never
        sees the per-call intermediates).
        """
        adapter = scripted(
            # Call 1: a tool_use round (continues the loop). Call 2: the answer.
            anthropic_reply(
                tool_calls=[("band_send_message", {"content": "hi"})],
                usage=anthropic_usage(100, 20),
            ),
            anthropic_reply("Hello!", usage=anthropic_usage(130, 8)),
            features=AdapterFeatures(emit={Emit.USAGE}),
        )
        await deliver(adapter, sample_message, mock_tools)

        # Exactly two model calls were made (the loop ran twice).
        assert adapter.client.call_count == 2
        # Find the single usage event and assert it carries the SUM (230/28),
        # not just the first (100/20) or last (130/8) call.
        usage_payloads = sent_usage_payloads(mock_tools)
        assert usage_payloads == [
            {
                "input_tokens": 230,
                "output_tokens": 28,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
            }
        ], f"expected one summed usage event, got {usage_payloads}"

    @pytest.mark.asyncio
    async def test_emits_accumulated_usage_when_loop_fails_midway(
        self, sample_message, mock_tools, scripted
    ):
        """A tool loop that raises after a successful call still emits that
        call's usage: tokens spent before the failure were still spent. The
        exception still propagates (the turn is marked failed)."""
        adapter = scripted(
            anthropic_reply(
                tool_calls=[("band_send_message", {"content": "hi"})],
                usage=anthropic_usage(100, 20),
            ),
            RuntimeError("boom"),
            features=AdapterFeatures(emit={Emit.USAGE}),
        )
        with pytest.raises(RuntimeError, match="boom"):
            await deliver(adapter, sample_message, mock_tools)

        usage_payloads = sent_usage_payloads(mock_tools)
        assert usage_payloads == [
            {
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
            }
        ], f"expected the first call's usage to be emitted, got {usage_payloads}"

    @pytest.mark.asyncio
    async def test_a_failed_turn_reports_its_own_rooms_usage(
        self, sample_message, mock_tools, scripted
    ):
        """One adapter serves every room, and their turns interleave.

        A failing turn reports its error to the room before its usage is
        emitted, and a second room's turn can start during that await — so
        usage read from a single "most recent turn" tally is whichever room
        called the model last, not this one's.
        """
        import asyncio

        other_room_ran = asyncio.Event()

        async def report_error(tools, message):  # noqa: ARG001
            other_room_ran.set()
            await asyncio.sleep(0)  # let the other room's turn take the backend

        adapter = scripted(
            # The failing room's call, then the busy room's.
            RuntimeError("boom"),
            anthropic_reply(usage=anthropic_usage(7, 3)),
            features=AdapterFeatures(emit={Emit.USAGE}),
        )

        with patch.object(adapter, "_report_error", new=report_error):
            failing = asyncio.create_task(
                deliver(adapter, sample_message, mock_tools, room_id="room-failing")
            )
            await other_room_ran.wait()
            await deliver(adapter, sample_message, mock_tools, room_id="room-busy")
            with pytest.raises(RuntimeError, match="boom"):
                await failing

        assert sent_usage_payloads(mock_tools) == [
            {
                "input_tokens": 7,
                "output_tokens": 3,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
            }
        ], "only the busy room spent tokens; the failing room must report none"

    @pytest.mark.asyncio
    async def test_interrupted_turn_surfaces_as_cancelled(
        self, sample_message, mock_tools, scripted
    ):
        """Interrupting a turn must reach the runtime as ``CancelledError``.

        ``ExecutionContext._run_cycle`` tells an interrupt apart from a handler
        error by catching ``CancelledError``; anything else raised out of the
        adapter's teardown makes a stop look like a crash, so the attempt is
        never un-charged. The turn's teardown must also still run to completion
        — history stays in sync — while doing no usage I/O on the way out.
        """
        import asyncio

        parked = asyncio.Event()

        async def park(**_payload):
            parked.set()
            await asyncio.sleep(30)  # interrupted here

        adapter = scripted(
            anthropic_reply(
                tool_calls=[("band_send_message", {"content": "hi"})],
                usage=anthropic_usage(100, 20),
            ),
            park,
            features=AdapterFeatures(emit={Emit.USAGE}),
        )
        turn = asyncio.create_task(deliver(adapter, sample_message, mock_tools))
        await parked.wait()
        turn.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn

        assert adapter.session_history("room-123"), (
            "teardown must finish syncing history before the cancel propagates"
        )
        assert sent_usage_payloads(mock_tools) == [], (
            "a cancelled turn must not start usage I/O on the way out"
        )

    @pytest.mark.asyncio
    async def test_cooperative_cancel_stops_the_tool_loop(
        self, sample_message, mock_tools, scripted
    ):
        """A flipped cancellation token must end the loop at the next round.

        ``AgentStream.aclose`` and a room interrupt both flip the turn's token
        first and only hard-cancel the task afterwards, so a façade that runs
        its own tool loop has to read that token — otherwise the loop keeps
        calling the model until something tears the task down.
        """
        import asyncio

        from band.core.run.cancellation import FlagCancellation
        from band.core.run.context import SimpleRunContext
        from tests.core.adapterhelpers import make_agent_input

        cancellation = FlagCancellation()

        async def cancel_mid_turn(*_args, **_kwargs):
            cancellation.cancel()
            return ToolCallOutcome(value={"status": "success"}, ok=True)

        mock_tools.execute_tool_call_structured = AsyncMock(side_effect=cancel_mid_turn)
        adapter = scripted(
            anthropic_reply(tool_calls=[("band_send_message", {"content": "hi"})]),
            anthropic_reply(tool_calls=[("band_send_message", {"content": "again"})]),
        )
        inp = make_agent_input(msg=sample_message, tools=mock_tools, room_id="room-123")

        with pytest.raises(asyncio.CancelledError):
            await run_adapter_turn(
                adapter,
                inp,
                context=SimpleRunContext(tools=mock_tools, cancellation=cancellation),
            )

        assert adapter.client.call_count == 1, (
            "the loop must not start another round once the turn is cancelled"
        )


class TestErrorHandling:
    """Tests for error handling."""

    @pytest.mark.asyncio
    async def test_reports_error_on_api_failure(
        self, sample_message, mock_tools, scripted
    ):
        """Should report error when Anthropic API fails."""
        adapter = scripted(RuntimeError("API Error"))
        await adapter.on_started("TestBot", "Test bot")

        with pytest.raises(RuntimeError, match="API Error"):
            await deliver(adapter, sample_message, mock_tools)

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
            additional_tools=[
                FunctionTool.from_custom_tool_def((EchoInput, echo_message))
            ],
        )

        assert len(adapter._custom_tools) == 1
        assert adapter._custom_tools[0][0] is EchoInput

    def test_accepts_multiple_custom_tools(self):
        """Adapter should accept multiple custom tools."""
        adapter = AnthropicAdapter(
            additional_tools=[
                FunctionTool.from_custom_tool_def((EchoInput, echo_message)),
                FunctionTool.from_custom_tool_def((CalculatorInput, calculate)),
            ],
        )

        assert len(adapter._custom_tools) == 2

    @pytest.mark.asyncio
    async def test_merges_custom_tool_schemas(
        self, sample_message, mock_tools, scripted
    ):
        """Custom tools should reach the model alongside platform tools."""
        adapter = scripted(
            anthropic_reply("Hi there"),
            additional_tools=[
                FunctionTool.from_custom_tool_def((EchoInput, echo_message))
            ],
        )
        await adapter.on_started("TestBot", "Test bot")
        mock_tools.get_anthropic_tool_schemas = MagicMock(
            return_value=[
                {"name": "band_send_message", "description": "Send a message"}
            ]
        )

        await deliver(adapter, sample_message, mock_tools)

        sent = [tool["name"] for tool in adapter.client.last_payload["tools"]]
        assert sorted(sent) == ["band_send_message", "echo"]

    @pytest.mark.asyncio
    async def test_custom_tool_result_reaches_next_provider_request(
        self, sample_message, mock_tools, scripted
    ):
        adapter = scripted(
            anthropic_reply(tool_calls=[("echo", {"message": "hello"})]),
            anthropic_reply(
                tool_calls=[
                    (
                        "band_send_message",
                        {"content": "Final answer", "mentions": [{"id": "user-456"}]},
                    )
                ]
            ),
            anthropic_reply("Done."),
            additional_tools=[
                FunctionTool.from_custom_tool_def((EchoInput, echo_message))
            ],
        )
        await adapter.on_started("TestBot", "Test bot")

        await deliver(adapter, sample_message, mock_tools)

        assert adapter.client.call_count == 3
        assert adapter.client.payloads[1]["messages"][-1] == {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call-1",
                    "content": "Echo: hello",
                    "is_error": False,
                }
            ],
        }
        mock_tools.execute_tool_call_structured.assert_awaited_once_with(
            "band_send_message",
            {"content": "Final answer", "mentions": [{"id": "user-456"}]},
        )


class TestProviderOptions:
    """Phase 2 sampling / provider façade."""

    def test_temperature_forwarded_to_provider(self) -> None:
        adapter = AnthropicAdapter(temperature=0.4, max_output_tokens=512)
        assert adapter._provider.sampling.temperature == 0.4
        assert adapter._provider.sampling.max_output_tokens == 512


class TestShutdown:
    @pytest.mark.asyncio
    async def test_stopping_the_agent_closes_the_provider_client(self):
        """The provider owns an HTTP client that only this path can close.

        Driven through the backend the agent actually stops, not through
        `cleanup_all` directly: the leak was that nothing connected the two.
        """
        
        adapter = AnthropicAdapter(provider_key="test-key")
        closed = False

        class ClosingClient:
            async def close(self) -> None:
                nonlocal closed
                closed = True

        adapter.client = ClosingClient()

        await adapter.cleanup_all()

        assert closed, "the provider's HTTP client outlived the agent"
