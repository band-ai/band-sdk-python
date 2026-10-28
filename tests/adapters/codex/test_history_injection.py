"""Codex adapter: history injection."""

from __future__ import annotations

from typing import Any

import pytest

from band.adapters.codex import (
    CodexAdapterConfig,
)
from band.integrations.codex import CodexJsonRpcError
from band.integrations.codex.types import (
    CodexRequestMethod,
)
from band.runtime.tools import BandTool
from tests.adapters.codex.kit import (
    ROOM_ID,
    ToolSchemaFakeTools,
    agent_input,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    final_text,
    make_codex_adapter,
    tool_call_request,
    turn_completed,
)
from tests.adapters.dispatch import deliver_event


class TestHistoryInjection:
    @pytest.mark.asyncio
    async def test_history_injected_on_resume_failure(self) -> None:
        """Resume fails, fresh thread created, first turn input contains history block."""
        events = [
            tool_call_request(1, BandTool.NO_REPLY),
            final_text("Done."),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(
            events=events,
            resume_error=CodexJsonRpcError(code=-32002, message="Thread expired"),
        )
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")

        raw_history = [
            {
                "message_type": "task",
                "content": "mapping event",
                "metadata": {"codex_thread_id": "thr-old", "codex_room_id": ROOM_ID},
            },
            {
                "message_type": "text",
                "content": "Can you refactor the auth module?",
                "sender_name": "Alice",
            },
            {
                "message_type": "text",
                "content": "Done — split into auth_handler.py and middleware.py",
                "sender_name": "CodexAgent",
            },
        ]

        inp = agent_input("Now add rate limiting", tools, raw_history=raw_history)
        await deliver_event(adapter, inp)

        turn_start = fake_client.params_of(CodexRequestMethod.TURN_START)[0]
        turn_input = turn_start["input"]
        history_items = [
            item for item in turn_input if "[Conversation History]" in item["text"]
        ]
        assert len(history_items) == 1
        assert "[Alice]: Can you refactor the auth module?" in history_items[0]["text"]
        assert (
            "[CodexAgent]: Done — split into auth_handler.py and middleware.py"
            in history_items[0]["text"]
        )
        # Task events should NOT appear in history context
        assert "mapping event" not in history_items[0]["text"]

    @pytest.mark.asyncio
    async def test_history_not_injected_on_successful_resume(self) -> None:
        """Resume succeeds, no history injection."""
        events = [
            tool_call_request(1, BandTool.NO_REPLY),
            final_text("Done."),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")

        raw_history = [
            {
                "message_type": "task",
                "content": "mapping",
                "metadata": {
                    "codex_thread_id": "thr-existing",
                    "codex_room_id": ROOM_ID,
                },
            },
            {
                "message_type": "text",
                "content": "Hello",
                "sender_name": "Alice",
            },
        ]

        inp = agent_input("Continue", tools, raw_history=raw_history)
        await deliver_event(adapter, inp)

        turn_start = fake_client.params_of(CodexRequestMethod.TURN_START)[0]
        turn_input = turn_start["input"]
        assert not any("[Conversation History]" in item["text"] for item in turn_input)

    @pytest.mark.asyncio
    async def test_history_not_injected_when_disabled(self) -> None:
        """inject_history_on_resume_failure=False, no injection even on failure."""
        events = [
            tool_call_request(1, BandTool.NO_REPLY),
            final_text("Done."),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(
            events=events,
            resume_error=CodexJsonRpcError(code=-32002, message="Thread expired"),
        )
        adapter = make_codex_adapter(
            fake_client,
            config=CodexAdapterConfig(inject_history_on_resume_failure=False),
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")

        raw_history = [
            {
                "message_type": "text",
                "content": "Hello",
                "sender_name": "Alice",
            },
        ]

        inp = agent_input("Continue", tools, raw_history=raw_history)
        await deliver_event(adapter, inp)

        turn_start = fake_client.params_of(CodexRequestMethod.TURN_START)[0]
        turn_input = turn_start["input"]
        assert not any("[Conversation History]" in item["text"] for item in turn_input)

    @pytest.mark.asyncio
    async def test_history_filters_non_text_messages(self) -> None:
        """Only canonical text messages appear in injected context."""
        events = [
            tool_call_request(1, BandTool.NO_REPLY),
            final_text("Done."),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(
            events=events,
            resume_error=CodexJsonRpcError(code=-32002, message="Not found"),
        )
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")

        raw_history = [
            {
                "message_type": "task",
                "content": "task event",
                "sender_name": "System",
                "metadata": {"codex_thread_id": "thr-old", "codex_room_id": ROOM_ID},
            },
            {
                "message_type": "tool_call",
                "content": '{"name": "foo"}',
                "sender_name": "Agent",
            },
            {
                "message_type": "tool_result",
                "content": "result",
                "sender_name": "Agent",
            },
            {
                "message_type": "thought",
                "content": "thinking...",
                "sender_name": "Agent",
            },
            {"message_type": "error", "content": "oops", "sender_name": "Agent"},
            {
                "message_type": "text",
                "content": "Hello world",
                "sender_name": "Alice",
            },
            {
                "message_type": "message",
                "content": "Hi there",
                "sender_name": "Bob",
            },
        ]

        inp = agent_input("Go", tools, raw_history=raw_history)
        await deliver_event(adapter, inp)

        turn_start = fake_client.params_of(CodexRequestMethod.TURN_START)[0]
        turn_input = turn_start["input"]
        history_items = [
            item for item in turn_input if "[Conversation History]" in item["text"]
        ]
        assert len(history_items) == 1
        text = history_items[0]["text"]
        assert "[Alice]: Hello world" in text
        # "message" is not a MessageType value; nothing on the platform
        # produces it, so it does not survive replay.
        assert "[Bob]: Hi there" not in text
        assert "task event" not in text
        assert "thinking..." not in text
        assert "oops" not in text
        assert "tool_call" not in text

    @pytest.mark.asyncio
    async def test_history_respects_max_messages(self) -> None:
        """Only last max_history_messages are injected."""
        events = [
            tool_call_request(1, BandTool.NO_REPLY),
            final_text("Done."),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(
            events=events,
            resume_error=CodexJsonRpcError(code=-32002, message="Not found"),
        )
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(max_history_messages=3)
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")

        raw_history: list[dict[str, Any]] = [
            {
                "message_type": "task",
                "content": "mapping",
                "metadata": {"codex_thread_id": "thr-old", "codex_room_id": ROOM_ID},
            },
        ]
        raw_history.extend(
            {
                "message_type": "text",
                "content": f"Message {i}",
                "sender_name": "Alice",
            }
            for i in range(10)
        )

        inp = agent_input("Go", tools, raw_history=raw_history)
        await deliver_event(adapter, inp)

        turn_start = fake_client.params_of(CodexRequestMethod.TURN_START)[0]
        turn_input = turn_start["input"]
        history_items = [
            item for item in turn_input if "[Conversation History]" in item["text"]
        ]
        assert len(history_items) == 1
        text = history_items[0]["text"]
        # Only last 3 messages should be present
        assert "Message 7" in text
        assert "Message 8" in text
        assert "Message 9" in text
        assert "Message 0" not in text
        assert "Message 6" not in text

    @pytest.mark.asyncio
    async def test_history_is_replayed_only_into_the_seeding_turn(self) -> None:
        """Once the seeding turn completes, later turns carry no transcript."""
        fake_client = FakeCodexClient(
            events=[
                tool_call_request(1, BandTool.NO_REPLY),
                turn_completed("turn-1"),
                tool_call_request(2, BandTool.NO_REPLY),
                turn_completed("turn-2"),
            ]
        )
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        await adapter.on_started("Codex Agent", "A coding agent")
        raw_history = [
            {"message_type": "text", "content": "Hello", "sender_name": "Alice"}
        ]

        await deliver_event(
            adapter, agent_input("Go", ToolSchemaFakeTools(), raw_history=raw_history)
        )
        await deliver_event(
            adapter,
            agent_input("Again", ToolSchemaFakeTools(), is_session_bootstrap=False),
        )

        seeding, later = fake_client.params_of(CodexRequestMethod.TURN_START)
        assert "[Alice]: Hello" in str(seeding["input"])
        assert "[Conversation History]" not in str(later["input"])
