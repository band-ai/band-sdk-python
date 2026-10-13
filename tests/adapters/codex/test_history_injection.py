"""Codex adapter: history injection."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from band.adapters.codex import (
    CodexAdapterConfig,
    PendingApproval,
)
from band.core.defaultmodels import OPENAI_MODEL
from band.core.protocols import TurnResultAlreadyReported
from band.integrations.codex import CodexJsonRpcError
from band.integrations.codex.types import (
    CodexRequestMethod,
    CodexSessionState,
)
from band.runtime.decisions import DecisionRegistry
from band.runtime.tools import BandTool, ToolCallOutcome
from band.testing import (
    events_of_type,
    reported_failures,
)
from tests.adapters.codex.kit import (
    ToolSchemaFakeTools,
    agent_input,
    make_platform_message,
    start_room_process,
    wire_codex_room,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    event_notification,
    final_text,
    make_codex_adapter,
    tool_call_request,
    turn_completed,
)
from tests.adapters.dispatch import deliver_event, deliver_message


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
                "metadata": {"codex_thread_id": "thr-old"},
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
                    "codex_room_id": "room-1",
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
                "metadata": {"codex_thread_id": "thr-old", "codex_room_id": "room-1"},
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
                "metadata": {"codex_thread_id": "thr-old", "codex_room_id": "room-1"},
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
    async def test_history_cleared_after_injection(self) -> None:
        """Raw history removed from memory after first turn."""
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
                "message_type": "text",
                "content": "Hello",
                "sender_name": "Alice",
            },
        ]

        inp = agent_input("Go", tools, raw_history=raw_history)
        await deliver_event(adapter, inp)

        # After injection, stashed data should be cleaned up
        assert "room-1" not in adapter._raw_history_by_room
        assert "room-1" not in adapter._needs_history_injection

    @pytest.mark.asyncio
    async def test_auto_selected_model_error_propagates_without_retry(self) -> None:
        """Auto-selected model errors propagate instead of trying another model."""
        fake_client = FakeCodexClient(
            turn_start_error=CodexJsonRpcError(
                code=-32000,
                message="Model gpt-5.5 is not available for this account",
            ),
            turn_start_error_once=False,
            model_list_result={
                "data": [
                    {"id": "gpt-5.5", "hidden": False},
                    {"id": "gpt-6-luna", "hidden": False},
                ]
            },
        )
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig(model=None))
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "An agent")

        msg = make_platform_message(room_id="room-1", content="hello")
        with pytest.raises(CodexJsonRpcError, match="not available"):
            await deliver_message(
                adapter,
                msg,
                tools,
                CodexSessionState(),
                None,
                None,
                is_session_bootstrap=False,
                room_id="room-1",
            )

        assert fake_client.request_methods.count(CodexRequestMethod.TURN_START) == 1
        assert adapter._selected_model == "gpt-5.5"

    @pytest.mark.asyncio
    async def test_model_selection_uses_first_visible_model(self) -> None:
        """Auto-selection uses Codex's first visible model without fallback ordering."""
        fake_client = FakeCodexClient(
            model_list_result={
                "data": [
                    {"id": "gpt-5.6-sol", "hidden": False},
                    {"id": OPENAI_MODEL, "hidden": False},
                ]
            },
        )
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig(model=None))
        await adapter.on_started("Agent", "An agent")
        await start_room_process(adapter)

        assert adapter._room_clients["room-1"].selected_model == "gpt-5.6-sol"

    @pytest.mark.asyncio
    async def test_explicit_model_error_propagates_without_fallback(self) -> None:
        """When the user explicitly set a model, errors propagate — no silent fallback."""
        fake_client = FakeCodexClient(
            turn_start_error=CodexJsonRpcError(
                code=-32000,
                message="Model unavailable-test-model is not available",
            ),
            turn_start_error_once=False,
            model_list_result={
                "data": [
                    {"id": "gpt-5.5", "hidden": False},
                    {"id": "gpt-6-luna", "hidden": False},
                ]
            },
        )
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(model="unavailable-test-model")
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "An agent")

        msg = make_platform_message(room_id="room-1", content="hello")
        with pytest.raises(CodexJsonRpcError, match="not available"):
            await deliver_message(
                adapter,
                msg,
                tools,
                CodexSessionState(),
                None,
                None,
                is_session_bootstrap=False,
                room_id="room-1",
            )

        assert CodexRequestMethod.MODEL_LIST not in fake_client.request_methods

        failures = reported_failures(tools)
        assert len(failures) == 1
        assert failures[0]["provider"] == "codex"
        assert "not available" in failures[0]["message"]

    @pytest.mark.asyncio
    async def test_model_selection_uses_default_when_model_list_empty(self) -> None:
        """Auto-selection uses the adapter default when Codex returns no visible models."""
        fake_client = FakeCodexClient(model_list_result={"data": []})
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig(model=None))
        await adapter.on_started("Agent", "An agent")
        await start_room_process(adapter)

        assert adapter._room_clients["room-1"].selected_model == OPENAI_MODEL

    @pytest.mark.asyncio
    async def test_model_selection_uses_default_when_model_list_fails(self) -> None:
        """Auto-selection uses the adapter default if model discovery fails."""
        fake_client = FakeCodexClient(
            model_list_error=RuntimeError("model/list unavailable")
        )
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig(model=None))
        await adapter.on_started("Agent", "An agent")
        await start_room_process(adapter)

        assert adapter._room_clients["room-1"].selected_model == OPENAI_MODEL

    @pytest.mark.asyncio
    async def test_non_model_error_propagates(self) -> None:
        """Non-model-related errors propagate from turn startup."""
        fake_client = FakeCodexClient(
            turn_start_error=CodexJsonRpcError(
                code=-32001, message="Server overloaded"
            ),
            turn_start_error_once=False,
        )
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig(model=None))
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "An agent")

        msg = make_platform_message(room_id="room-1", content="hello")
        with pytest.raises(CodexJsonRpcError, match="overloaded"):
            await deliver_message(
                adapter,
                msg,
                tools,
                CodexSessionState(),
                None,
                None,
                is_session_bootstrap=False,
                room_id="room-1",
            )

    @pytest.mark.asyncio
    async def test_startup_config_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Startup emits a redacted config summary log line."""
        fake_client = FakeCodexClient()
        adapter = make_codex_adapter(
            fake_client,
            config=CodexAdapterConfig(
                model="gpt-5.5", sandbox="workspace-write", approval_mode="manual"
            ),
        )

        with caplog.at_level("INFO", logger="band.adapters.codex"):
            await adapter.on_started("TestBot", "A test agent")

        startup_logs = [
            r for r in caplog.records if "Codex adapter started" in r.message
        ]
        assert len(startup_logs) == 1
        log_msg = startup_logs[0].message
        assert "agent=TestBot" in log_msg
        assert "transport=stdio" in log_msg
        assert "model=gpt-5.5" in log_msg
        assert "sandbox=workspace-write" in log_msg
        assert "approval_mode=manual" in log_msg

    @pytest.mark.asyncio
    async def test_codex_error_emits_event_unconditionally(self) -> None:
        """Non-retryable Codex errors always emit a structured error event."""
        fake_client = FakeCodexClient(
            events=[
                event_notification(
                    "error",
                    {"error": {"message": "Something went wrong"}, "willRetry": False},
                ),
                event_notification(
                    "turn/completed",
                    {"turn": {"id": "turn-1", "status": "failed"}},
                ),
            ],
        )
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "An agent")

        msg = make_platform_message(room_id="room-1", content="do something")
        with pytest.raises(TurnResultAlreadyReported):
            await deliver_message(
                adapter,
                msg,
                tools,
                CodexSessionState(),
                None,
                None,
                is_session_bootstrap=False,
                room_id="room-1",
            )

        error_events = events_of_type(tools, "error")
        assert len(error_events) == 1
        assert "Something went wrong" in error_events[0]["content"]

    @pytest.mark.asyncio
    async def test_cleanup_before_start(self) -> None:
        """Calling on_cleanup on a freshly constructed adapter should not raise."""
        adapter = make_codex_adapter(
            FakeCodexClient(),
            config=CodexAdapterConfig(),
        )
        # No on_started called — cleanup should be safe (idempotent)
        await adapter.on_cleanup("room-x")

    @pytest.mark.asyncio
    async def test_cleanup_clears_pending_approvals(self) -> None:
        """on_cleanup should evict all pending approvals for the given room."""
        fake_client = FakeCodexClient()
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        await adapter.on_started("Bot", "desc")

        # Manually inject a pending approval for room-1
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        registry: DecisionRegistry[PendingApproval] = DecisionRegistry()
        registry.register_keyed(
            PendingApproval(
                request_id=1,
                method="item/tool/call",
                summary="test",
                created_at=datetime.now(UTC),
                future=fut,
            ),
            key="tok-1",
        )
        adapter._pending_approvals["room-1"] = registry
        wire_codex_room(adapter, fake_client, "room-1")
        adapter._room_threads["room-1"] = "thr-1"

        await adapter.on_cleanup("room-1")

        assert "room-1" not in adapter._pending_approvals
        # The future should have been resolved (declined)
        assert fut.done()

    @pytest.mark.asyncio
    async def test_tool_call_validation_error_returns_friendly_message(self) -> None:
        """A base-tool arg-validation failure returns a user-friendly error.

        AgentTools catches base-tool validation INSIDE execute_tool_call_structured and
        returns ok=False with a friendly message (it does not raise), so the adapter
        surfaces it via the ok=False path.
        """

        class ValidationErrorTools(ToolSchemaFakeTools):
            async def execute_tool_call_structured(
                self, tool_name: str, arguments: dict[str, Any]
            ) -> ToolCallOutcome:
                self.tool_calls.append({"tool_name": tool_name, "arguments": arguments})
                return ToolCallOutcome(
                    value="Invalid arguments for band_send_message: content: Field required",
                    ok=False,
                    error_message="content: Field required",
                )

        events = [
            tool_call_request(99, BandTool.SEND_MESSAGE),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ValidationErrorTools()

        await adapter.on_started("Bot", "desc")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools,
            CodexSessionState(),
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # The adapter should have responded to the tool call with success=False
        error_responses = [
            (rid, payload)
            for rid, payload in fake_client.responses
            if payload.get("success") is False
        ]
        assert len(error_responses) == 1
        error_text = error_responses[0][1]["contentItems"][0]["text"]
        assert "Invalid arguments for band_send_message" in error_text
