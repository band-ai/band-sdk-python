"""Tests for GeminiAdapter."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.adapters.gemini import GeminiAdapter
from band.core.types import AdapterFeatures, Emit, PlatformMessage
from band.runtime.tools import ToolCallOutcome
from tests.modelclients import ScriptedGeminiClient, gemini_reply


@pytest.fixture
def sample_message() -> PlatformMessage:
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
def mock_tools() -> MagicMock:
    """Create mock AgentToolsProtocol (MagicMock base, AsyncMock methods)."""
    tools = MagicMock()
    tools.get_openai_tool_schemas = MagicMock(return_value=[])
    tools.send_message = AsyncMock(return_value={"status": "sent"})
    tools.send_event = AsyncMock(return_value={"status": "sent"})
    tools.execute_tool_call_structured = AsyncMock(
        return_value=ToolCallOutcome(value={"status": "success"}, ok=True)
    )
    return tools


@pytest.fixture
def scripted():
    """Build an adapter whose Gemini client replays scripted replies.

    Injection at the SDK client keeps the provider's real request projection
    and response mapping inside every turn these tests drive, and the returned
    client records the payloads that reached the wire.
    """

    def build(*replies, **kwargs) -> GeminiAdapter:
        adapter = GeminiAdapter(provider_key="test-key", **kwargs)
        adapter.client = ScriptedGeminiClient(list(replies))
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


class TestOnStarted:
    @pytest.mark.asyncio
    async def test_renders_system_prompt(self):
        adapter = GeminiAdapter(provider_key="test-key")
        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")
        assert adapter._system_prompt != ""
        assert "TestBot" in adapter._system_prompt

    @pytest.mark.asyncio
    async def test_uses_replace_instructions_when_provided(self):
        from band.core.instructions import Instruction, InstructionMode

        adapter = GeminiAdapter(
            instructions=Instruction(
                text="Custom prompt here.",
                mode=InstructionMode.REPLACE,
            ),
            provider_key="test-key",
        )
        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")
        assert adapter._system_prompt == "Custom prompt here."


class TestOnMessage:
    @pytest.mark.asyncio
    async def test_initializes_history_on_bootstrap(
        self, sample_message, mock_tools, scripted
    ):
        adapter = scripted(gemini_reply("ok"))
        await adapter.on_started("TestBot", "Test bot")

        await deliver(adapter, sample_message, mock_tools)

        assert len(adapter.session_history("room-123")) >= 2

    @pytest.mark.asyncio
    async def test_system_prompt_and_tools_reach_the_wire(
        self, sample_message, mock_tools, scripted
    ):
        """The rendered instructions and the room's tool declarations are what
        the provider actually sends — nothing between the adapter and the SDK
        drops them."""
        adapter = scripted(gemini_reply("ok"))
        mock_tools.get_openai_tool_schemas = MagicMock(
            return_value=[
                {
                    "type": "function",
                    "function": {"name": "band_send_message", "description": "Send"},
                }
            ]
        )
        await adapter.on_started("TestBot", "Test bot")

        await deliver(adapter, sample_message, mock_tools)

        config = adapter.client.last_payload["config"]
        assert config.system_instruction == adapter._system_prompt
        declared = [d.name for tool in config.tools for d in tool.function_declarations]
        assert declared == ["band_send_message"]

    @pytest.mark.asyncio
    async def test_executes_tool_loop(self, sample_message, mock_tools, scripted):
        adapter = scripted(
            gemini_reply(tool_calls=[("band_lookup_peers", {"page": "1"})]),
            gemini_reply("done"),
            features=AdapterFeatures(emit={Emit.EXECUTION}),
        )
        await adapter.on_started("TestBot", "Test bot")

        await deliver(adapter, sample_message, mock_tools)

        mock_tools.execute_tool_call_structured.assert_awaited_once_with(
            "band_lookup_peers", {"page": "1"}
        )
        # tool_call + tool_result reporting
        assert mock_tools.send_event.call_count == 2

    @pytest.mark.asyncio
    async def test_send_event_failure_does_not_crash_tool_execution(
        self, sample_message, mock_tools, scripted
    ):
        adapter = scripted(
            gemini_reply(tool_calls=[("band_lookup_peers", {"page": "1"})]),
            gemini_reply("done"),
            features=AdapterFeatures(emit={Emit.EXECUTION}),
        )
        await adapter.on_started("TestBot", "Test bot")
        mock_tools.send_event.side_effect = Exception("403 Forbidden")

        await deliver(adapter, sample_message, mock_tools)

        mock_tools.execute_tool_call_structured.assert_awaited_once()


class TestBuildGeminiTools:
    """Gemini rejects numeric bounds and additionalProperties on tool params, so
    the adapter must sanitize Band schemas before building declarations."""

    def test_declarations_drop_numeric_bounds_and_additional_properties(
        self, mock_tools
    ):
        mock_tools.get_openai_tool_schemas = MagicMock(
            return_value=[
                {
                    "type": "function",
                    "function": {
                        "name": "band_lookup_peers",
                        "description": "lookup peers",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "page_size": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "maximum": 100,
                                },
                            },
                            "additionalProperties": False,
                        },
                    },
                }
            ]
        )
        adapter = GeminiAdapter(provider_key="test-key")

        tools = adapter._build_tools(mock_tools)

        decl = tools[0].function_declarations[0]
        schema = decl.parameters_json_schema
        assert "additionalProperties" not in schema
        page_size = schema["properties"]["page_size"]
        assert "minimum" not in page_size
        assert "maximum" not in page_size


class TestOnCleanup:
    @pytest.mark.asyncio
    async def test_removes_room_history(self):
        adapter = GeminiAdapter(provider_key="test-key")
        adapter._backend.bind_session("room-1", [])
        await adapter.on_cleanup("room-1")
        assert not adapter._backend.has_session("room-1")

    @pytest.mark.asyncio
    async def test_cleanup_twice_is_idempotent(self):
        adapter = GeminiAdapter(provider_key="test-key")
        adapter._backend.bind_session("room-1", [])
        await adapter.on_cleanup("room-1")
        await adapter.on_cleanup("room-1")  # Should not raise
        assert not adapter._backend.has_session("room-1")

    @pytest.mark.asyncio
    async def test_cleanup_unknown_room_is_noop(self):
        adapter = GeminiAdapter(provider_key="test-key")
        await adapter.on_cleanup("nonexistent-room")  # Should not raise
        assert not adapter._backend.has_session("nonexistent-room")

    @pytest.mark.asyncio
    async def test_cleanup_before_any_messages(self):
        adapter = GeminiAdapter(provider_key="test-key")
        await adapter.on_started("TestBot", "Test bot")
        await adapter.on_cleanup("room-never-used")  # Should not raise


class TestMaxToolRounds:
    @pytest.mark.asyncio
    async def test_raises_runtime_error_when_max_rounds_exceeded(
        self, sample_message, mock_tools, scripted
    ):
        # Every round asks for a tool, so the loop never terminates naturally.
        always_calling = gemini_reply(tool_calls=[("band_lookup_peers", {"page": "1"})])
        adapter = scripted(always_calling, always_calling, max_tool_rounds=2)
        await adapter.on_started("TestBot", "Test bot")

        with pytest.raises(RuntimeError, match="Exceeded max tool rounds"):
            await deliver(adapter, sample_message, mock_tools)

    @pytest.mark.asyncio
    async def test_exhausted_rounds_are_reported_to_the_room(
        self, sample_message, mock_tools, scripted
    ):
        """A runaway loop must not end the turn silently.

        Hitting the cap raises, which the runtime logs — but the room only
        learns anything if the turn reports it like any other failure.
        """
        always_calling = gemini_reply(tool_calls=[("band_lookup_peers", {"page": "1"})])
        adapter = scripted(always_calling, max_tool_rounds=1)
        await adapter.on_started("TestBot", "Test bot")

        with pytest.raises(RuntimeError, match="Exceeded max tool rounds"):
            await deliver(adapter, sample_message, mock_tools)

        assert [
            call.kwargs["message_type"] for call in mock_tools.send_event.call_args_list
        ] == ["error"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("max_tool_rounds", [0, -1])
    async def test_non_positive_limits_reject_before_calling_the_model(
        self, sample_message, mock_tools, scripted, max_tool_rounds
    ):
        adapter = scripted(max_tool_rounds=max_tool_rounds)
        await adapter.on_started("TestBot", "Test bot")

        with pytest.raises(RuntimeError, match="Exceeded max tool rounds"):
            await deliver(adapter, sample_message, mock_tools)

        assert adapter.client.call_count == 0


class TestInterruptedTurn:
    @pytest.mark.asyncio
    async def test_cancelled_turn_never_calls_the_model(
        self, sample_message, mock_tools, scripted
    ):
        """The turn's cancellation token gates the loop, not just task cancel.

        the adapter turn carries the token on the turn's tools because
        ``on_message`` cannot take it; a façade that ignores it keeps calling
        the model after ``AgentStream.aclose`` flips it.
        """
        import asyncio

        from band.core.run.cancellation import FlagCancellation
        from tests.core.contractsupport import turn_tools

        cancellation = FlagCancellation()
        cancellation.cancel()
        adapter = scripted(gemini_reply("hi"))
        await adapter.on_started("TestBot", "Test bot")

        with pytest.raises(asyncio.CancelledError):
            await deliver(
                adapter,
                sample_message,
                turn_tools(mock_tools, cancellation=cancellation),
            )

        assert adapter.client.call_count == 0

    @pytest.mark.asyncio
    async def test_interrupt_surfaces_as_cancelled_and_finishes_teardown(
        self, sample_message, mock_tools, scripted
    ):
        """Interrupting a turn must reach the runtime as ``CancelledError``.

        ``ExecutionContext._run_cycle`` tells an interrupt apart from a handler
        error by catching ``CancelledError``, so teardown must not raise
        anything else. Teardown must also run to the end: history is trimmed
        *and* the backend session realigned to it, or the next turn replays a
        window the adapter already dropped.
        """
        import asyncio

        parked = asyncio.Event()

        async def park(**_payload):
            parked.set()
            await asyncio.sleep(30)  # interrupted here

        adapter = scripted(
            gemini_reply(tool_calls=[("band_lookup_peers", {"page": "1"})]),
            park,
            max_history_messages=2,
        )
        await adapter.on_started("TestBot", "Test bot")
        mock_tools.execute_tool_call_structured = AsyncMock(
            return_value=ToolCallOutcome(value={"status": "success"}, ok=True)
        )

        turn = asyncio.create_task(deliver(adapter, sample_message, mock_tools))
        await parked.wait()
        turn.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn

        history = adapter.session_history("room-123")
        assert len(history) <= adapter.max_history_messages, (
            f"teardown must trim history to the cap, got {len(history)}"
        )
        assert len(adapter._backend.session("room-123")) == len(history), (
            "teardown must realign the backend session with the trimmed history"
        )


class TestParticipantsContactsInjection:
    @pytest.mark.asyncio
    async def test_participants_msg_injected_into_history(
        self, sample_message, mock_tools, scripted
    ):
        adapter = scripted(gemini_reply("ok"))
        await adapter.on_started("TestBot", "Test bot")

        await deliver(
            adapter,
            sample_message,
            mock_tools,
            participants_msg="Alice, Bob are in the room",
        )

        history = adapter.session_history("room-123")
        # First entry should be participants system message
        assert "[System]: Alice, Bob are in the room" in history[0].parts[0].text

    @pytest.mark.asyncio
    async def test_contacts_msg_injected_into_history(
        self, sample_message, mock_tools, scripted
    ):
        adapter = scripted(gemini_reply("ok"))
        await adapter.on_started("TestBot", "Test bot")

        await deliver(
            adapter,
            sample_message,
            mock_tools,
            contacts_msg="Charlie is now a contact",
        )

        history = adapter.session_history("room-123")
        assert "[System]: Charlie is now a contact" in history[0].parts[0].text

    @pytest.mark.asyncio
    async def test_both_participants_and_contacts_injected(
        self, sample_message, mock_tools, scripted
    ):
        adapter = scripted(gemini_reply("ok"))
        await adapter.on_started("TestBot", "Test bot")

        await deliver(
            adapter,
            sample_message,
            mock_tools,
            participants_msg="Alice, Bob",
            contacts_msg="Charlie added",
        )

        history = adapter.session_history("room-123")
        # All user-side content merged into a single Content entry
        user_entry = history[0]
        assert len(user_entry.parts) == 3
        assert "[System]: Alice, Bob" in user_entry.parts[0].text
        assert "[System]: Charlie added" in user_entry.parts[1].text
        assert "Alice" in user_entry.parts[2].text  # user message
