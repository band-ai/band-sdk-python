"""Codex adapter flow: a room resumes its Codex thread from Band history, or rebuilds context when it cannot."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from band.adapters.codex import CodexAdapterConfig
from band.integrations.codex import CodexJsonRpcError, CodexRequestMethod
from tests.adapters.codex.kit import ToolSchemaFakeTools
from tests.adapters.codexturns import (
    FakeCodexClient,
    make_codex_adapter,
)
from tests.integrations.codex.kit import agent_input, answered_turn


class TestThreadResumption:
    """A room resumes its Codex thread from Band history, or rebuilds context when it cannot."""

    async def test_on_event_uses_converter_history_to_resume_thread(self) -> None:
        tools = ToolSchemaFakeTools()
        fake_client = FakeCodexClient(events=answered_turn())
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        await adapter.on_started("Codex Agent", "Integration test agent")

        raw_history = [
            {
                "message_type": "task",
                "content": "historical mapping",
                "metadata": {
                    "codex_thread_id": "thr-history",
                    "codex_room_id": "room-1",
                    "codex_created_at": datetime.now(UTC).isoformat(),
                },
            }
        ]
        await adapter.on_event(
            agent_input(
                "continue work",
                tools,
                raw_history=raw_history,
                room_id="room-1",
                is_session_bootstrap=True,
            )
        )

        methods = fake_client.request_methods
        assert CodexRequestMethod.THREAD_RESUME in methods
        assert CodexRequestMethod.THREAD_START not in methods
        assert any("Status: resumed" in event["content"] for event in tools.events_sent)

    @pytest.mark.asyncio
    async def test_restart_rehydrates_mapping_from_previous_task_events(self) -> None:
        tools_first = ToolSchemaFakeTools()
        fake_client_first = FakeCodexClient(events=answered_turn())
        adapter_first = make_codex_adapter(
            fake_client_first, config=CodexAdapterConfig()
        )
        await adapter_first.on_started("Codex Agent", "Integration test agent")
        await adapter_first.on_event(
            agent_input(
                "first turn",
                tools_first,
                room_id="room-9",
                is_session_bootstrap=True,
            )
        )

        persisted_history = list(tools_first.events_sent)

        tools_second = ToolSchemaFakeTools()
        fake_client_second = FakeCodexClient(events=answered_turn())
        adapter_second = make_codex_adapter(
            fake_client_second, config=CodexAdapterConfig()
        )
        await adapter_second.on_started("Codex Agent", "Integration test agent")
        await adapter_second.on_event(
            agent_input(
                "second turn after restart",
                tools_second,
                raw_history=persisted_history,
                room_id="room-9",
                is_session_bootstrap=True,
            )
        )

        methods_second = fake_client_second.request_methods
        assert CodexRequestMethod.THREAD_RESUME in methods_second
        assert CodexRequestMethod.THREAD_START not in methods_second

    @pytest.mark.asyncio
    async def test_resume_failure_injects_conversation_history(self) -> None:
        """Full lifecycle: first session produces history, second session resume fails,
        verify turn input contains history context."""
        # --- First session: produce some history ---
        tools_first = ToolSchemaFakeTools()
        fake_client_first = FakeCodexClient(events=answered_turn())
        adapter_first = make_codex_adapter(
            fake_client_first, config=CodexAdapterConfig()
        )
        await adapter_first.on_started("Codex Agent", "Integration test agent")
        await adapter_first.on_event(
            agent_input(
                "refactor the auth module",
                tools_first,
                room_id="room-hist",
                is_session_bootstrap=True,
            )
        )

        # Simulate persisted history: task events + text messages
        persisted_history = list(tools_first.events_sent)
        persisted_history.append(
            {
                "message_type": "text",
                "content": "refactor the auth module",
                "sender_name": "Alice",
            }
        )
        persisted_history.append(
            {
                "message_type": "text",
                "content": "Done — split into auth_handler.py and middleware.py",
                "sender_name": "CodexAgent",
            }
        )

        # --- Second session: resume fails, should inject history ---
        tools_second = ToolSchemaFakeTools()
        fake_client_second = FakeCodexClient(
            events=answered_turn(),
            resume_error=CodexJsonRpcError(code=-32002, message="Thread expired"),
        )
        adapter_second = make_codex_adapter(
            fake_client_second, config=CodexAdapterConfig()
        )
        await adapter_second.on_started("Codex Agent", "Integration test agent")
        await adapter_second.on_event(
            agent_input(
                "now add rate limiting",
                tools_second,
                raw_history=persisted_history,
                room_id="room-hist",
                is_session_bootstrap=True,
            )
        )

        methods = fake_client_second.request_methods
        assert CodexRequestMethod.THREAD_RESUME in methods
        assert CodexRequestMethod.THREAD_START in methods

        turn_start = fake_client_second.params_of(CodexRequestMethod.TURN_START)[0]
        turn_input = turn_start["input"]
        history_items = [
            item for item in turn_input if "[Conversation History]" in item["text"]
        ]
        assert len(history_items) == 1
        assert "[Alice]: refactor the auth module" in history_items[0]["text"]
        assert (
            "[CodexAgent]: Done — split into auth_handler.py and middleware.py"
            in history_items[0]["text"]
        )
