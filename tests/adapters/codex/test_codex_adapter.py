"""Codex adapter: codex adapter."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from band.adapters.codex import (
    CodexAdapter,
    CodexAdapterConfig,
)
from band.core.protocols import TurnResultAlreadyReported
from band.core.types import Emit
from band.integrations.codex import TRANSPORT_CLOSED, CodexJsonRpcError
from band.integrations.codex.types import (
    CodexRequestMethod,
    CodexSessionState,
    CodexTokenUsage,
)
from band.runtime.custom_tools import CustomToolDef
from band.runtime.tools import BandTool, ToolCallOutcome
from band.testing import (
    events_of_type,
    reported_failures,
)
from tests.adapters.codex.kit import (
    LIVE_EFFORTS_MODEL_LIST,
    ROOM_ID,
    CodexRoom,
    ToolSchemaFakeTools,
    make_platform_message,
    patch_codex_clients_by_room,
    send_bootstrap,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    agent_message_completed,
    event_notification,
    event_request,
    make_codex_adapter,
    tool_call_request,
    turn_completed,
)
from tests.adapters.dispatch import deliver_message


class TestCodexAdapter:
    def test_config_defaults_are_low_noise_and_manual_approval(
        self, assert_no_leaked_adapter_config_env: None
    ) -> None:
        config = CodexAdapterConfig()
        assert config.emit_turn_task_markers is False
        assert config.approval_mode == "manual"

    @pytest.mark.asyncio
    async def test_bootstrap_starts_thread_without_relaying_native_deltas(self) -> None:
        events = [
            event_notification(
                "item/agentMessage/delta",
                {"itemId": "msg-1", "delta": "harness-ok"},
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert CodexRequestMethod.THREAD_START in fake_client.request_methods
        thread_start = fake_client.params_of(CodexRequestMethod.THREAD_START)[0]
        assert "dynamicTools" in thread_start
        dynamic_names = [t["name"] for t in thread_start["dynamicTools"]]
        assert "band_send_message" in dynamic_names
        assert "band_send_event" in dynamic_names

        assert tools.messages_sent == []
        assert not tools.turn.complete

    @pytest.mark.asyncio
    async def test_assistant_text_as_thought_posts_no_reply(self) -> None:
        """Completed native text is narration, without mentions or settlement."""
        events = [
            agent_message_completed("(Waiting on the review.)", "msg-1"),
            turn_completed(),
        ]
        adapter = make_codex_adapter(
            FakeCodexClient(events=events),
            config=CodexAdapterConfig(),
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert tools.messages_sent == []
        thoughts = [e for e in tools.events_sent if e["message_type"] == "thought"]
        assert [e["content"] for e in thoughts] == ["(Waiting on the review.)"]
        assert not tools.turn.complete

    @pytest.mark.parametrize(
        "field", ["fallback_send_agent_text", "assistant_text_mode"]
    )
    def test_retired_delivery_config_is_rejected(self, field: str) -> None:
        with pytest.raises(ValidationError, match="Extra inputs"):
            CodexAdapterConfig.model_validate({field: True})

    @pytest.mark.asyncio
    async def test_system_prompt_retry_after_turn_start_failure(self) -> None:
        """System instructions stay pending until turn/start succeeds."""
        events = [
            turn_completed(),
        ]
        fake_client = FakeCodexClient(
            events=events,
            turn_start_error=CodexJsonRpcError(
                code=-32000,
                message="Model not available",
            ),
            turn_start_error_once=True,
        )
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(model="gpt-5.5")
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")

        with pytest.raises(CodexJsonRpcError, match="not available"):
            await deliver_message(
                adapter,
                make_platform_message(room_id="room-1", content="first try"),
                tools,
                CodexSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )
        assert "room-1" not in adapter._prompt_injected_rooms

        await deliver_message(
            adapter,
            make_platform_message(room_id="room-1", content="second try"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )
        assert "room-1" in adapter._prompt_injected_rooms

        turn_inputs = [
            params["input"]
            for params in fake_client.params_of(CodexRequestMethod.TURN_START)
        ]
        assert len(turn_inputs) == 2
        for turn_input in turn_inputs:
            assert any(
                item.get("text", "").startswith("[System Instructions]\n")
                for item in turn_input
            )

    @pytest.mark.asyncio
    async def test_tool_call_request_is_dispatched_and_responded(self) -> None:
        events = [
            tool_call_request(42, "band_lookup_peers", {"page": 1, "page_size": 10}),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert len(tools.tool_calls) == 1
        assert tools.tool_calls[0]["tool_name"] == "band_lookup_peers"
        assert fake_client.responses
        response_id, response_payload = fake_client.responses[0]
        assert response_id == 42
        assert response_payload["success"] is True

    @pytest.mark.asyncio
    async def test_failed_reply_leaves_native_text_as_unsettled_thought(
        self,
    ) -> None:
        """Fallback agent text should still be delivered when send_message fails.

        The failure is a non-raising ok=False (bad args / API error) — the case the
        plain execute_tool_call would misread as success and wrongly suppress.
        """

        class SendMessageFailureTools(ToolSchemaFakeTools):
            async def execute_tool_call_structured(
                self, tool_name: str, arguments: dict[str, Any]
            ) -> ToolCallOutcome:
                self.tool_calls.append({"tool_name": tool_name, "arguments": arguments})
                if tool_name == "band_send_message":
                    return ToolCallOutcome(
                        value="Error executing band_send_message: send failed",
                        ok=False,
                        error_message="send failed",
                    )
                return await super().execute_tool_call_structured(tool_name, arguments)

        events = [
            event_request(
                77,
                "item/tool/call",
                {
                    "tool": "band_send_message",
                    "arguments": {"content": "hi"},
                    "callId": "call-77",
                },
            ),
            event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "agentMessage",
                        "id": "msg-1",
                        "text": "fallback final text",
                    }
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = SendMessageFailureTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert tools.messages_sent == []
        assert not tools.turn.complete
        assert [e["content"] for e in events_of_type(tools, "thought")] == [
            "fallback final text"
        ]
        assert len(fake_client.responses) == 1
        _, payload = fake_client.responses[0]
        assert payload["success"] is False

    @pytest.mark.asyncio
    async def test_resume_failure_falls_back_to_thread_start(self) -> None:
        events = [turn_completed()]
        fake_client = FakeCodexClient(
            events=events,
            resume_error=CodexJsonRpcError(code=-32002, message="Not found"),
        )
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools,
            CodexSessionState(thread_id="thr-old", room_id="room-1"),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        methods = fake_client.request_methods
        assert CodexRequestMethod.THREAD_RESUME in methods
        assert CodexRequestMethod.THREAD_START in methods

    @pytest.mark.asyncio
    async def test_approval_request_auto_decline(self) -> None:
        events = [
            event_request(
                7,
                "item/commandExecution/requestApproval",
                {"command": "rm -rf tmp"},
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(approval_mode="auto_decline")
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert fake_client.responses
        response_id, payload = fake_client.responses[0]
        assert response_id == 7
        assert payload["decision"] == "decline"
        assert len(tools.messages_sent) == 1
        assert "Approval requested" in tools.messages_sent[0]["content"]
        assert "rm -rf tmp" in tools.messages_sent[0]["content"]

    @pytest.mark.asyncio
    async def test_auto_approval_responds_even_if_notification_fails(self) -> None:
        class FailingNotifyTools(ToolSchemaFakeTools):
            async def send_notice(
                self, content: str, mentions: list[dict[str, str]] | None = None
            ) -> Any:
                raise RuntimeError("notification failed")

        events = [
            event_request(
                7,
                "item/commandExecution/requestApproval",
                {"command": "rm -rf tmp"},
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client,
            config=CodexAdapterConfig(
                approval_mode="auto_decline", approval_text_notifications=True
            ),
        )
        tools = FailingNotifyTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert fake_client.responses
        response_id, payload = fake_client.responses[0]
        assert response_id == 7
        assert payload["decision"] == "decline"

    @pytest.mark.asyncio
    async def test_manual_approval_responds_with_decline_if_notification_fails(
        self,
    ) -> None:
        class FailingNotifyTools(ToolSchemaFakeTools):
            async def send_notice(
                self, content: str, mentions: list[dict[str, str]] | None = None
            ) -> Any:
                raise RuntimeError("notification failed")

        events = [
            event_request(
                7,
                "item/commandExecution/requestApproval",
                {"command": "rm -rf tmp"},
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(approval_mode="manual")
        )
        tools = FailingNotifyTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert fake_client.responses
        response_id, payload = fake_client.responses[0]
        assert response_id == 7
        assert payload["decision"] == "decline"
        assert "room-1" not in adapter._pending_approvals

        # The Band-delivery hiccup that caused this auto-decline must itself
        # be reported -- otherwise it's indistinguishable from a genuine
        # human decision, with no signal at all that anything went wrong.
        failures = reported_failures(tools)
        assert len(failures) == 1
        assert failures[0]["provider"] == "codex"

        # The human sender was never actually notified, so the audit trail
        # must not credit/blame them for this decision -- it was forced by
        # the delivery failure, same as every other forced-decline path.
        audit_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_event_type") == "approval_resolution"
        ]
        assert len(audit_events) == 1
        assert audit_events[0]["metadata"]["codex_decided_by"] == "system_fallback"

    @pytest.mark.asyncio
    async def test_cleanup_closes_client_when_last_room_removed(self) -> None:
        fake_client = FakeCodexClient(events=[turn_completed()])
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(room_id="room-1"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert fake_client.closed is False
        await adapter.on_cleanup("room-1")
        assert fake_client.closed is True

    @pytest.mark.asyncio
    async def test_cleanup_idempotent(self) -> None:
        """Calling on_cleanup twice for the same room should not raise."""
        fake_client = FakeCodexClient(events=[turn_completed()])
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(room_id="room-1"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        await adapter.on_cleanup("room-1")
        assert fake_client.closed is True
        # Second cleanup should not raise
        await adapter.on_cleanup("room-1")

    @pytest.mark.asyncio
    async def test_cleanup_multi_room_closes_each_room_client(self) -> None:
        """Each room owns its Codex client; cleanup closes only that room's client."""
        clients = {
            "room-1": FakeCodexClient(events=[turn_completed()]),
            # Each room owns a separate FakeCodexClient with its own turn
            # counter, so room-2's first turn is also "turn-1" -- matching
            # this room's client, not a global counter.
            "room-2": FakeCodexClient(events=[turn_completed()]),
        }
        adapter = CodexAdapter(config=CodexAdapterConfig())
        patch_codex_clients_by_room(adapter, clients)
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        for room_id in ("room-1", "room-2"):
            await deliver_message(
                adapter,
                make_platform_message(room_id=room_id),
                tools,
                CodexSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id=room_id,
            )

        await adapter.on_cleanup("room-1")
        assert clients["room-1"].closed is True
        assert clients["room-2"].closed is False

        await adapter.on_cleanup("room-2")
        assert clients["room-2"].closed is True

    @pytest.mark.asyncio
    async def test_forwards_raw_codex_task_events(self) -> None:
        events = [
            event_notification(
                "codex/event/task_started",
                {"taskId": "task-1", "task": {"title": "Inspect repository"}},
            ),
            event_notification(
                "codex/event/task_complete",
                {"taskId": "task-1", "summary": "Inspection finished"},
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        raw_task_events = [
            event
            for event in tools.events_sent
            if event["metadata"].get("codex_event_method")
            in {
                "codex/event/task_started",
                "codex/event/task_complete",
            }
        ]
        assert len(raw_task_events) == 2
        assert raw_task_events[0]["content"] == (
            "UUID: task-1\nTask: Inspect repository\nStatus: started"
        )
        assert raw_task_events[0]["metadata"]["codex_task_id"] == "task-1"
        assert raw_task_events[1]["content"] == (
            "UUID: task-1\nTask: Inspect repository\nStatus: completed\n"
            "Summary: Inspection finished"
        )
        assert raw_task_events[1]["metadata"]["codex_task_phase"] == "completed"

    @pytest.mark.asyncio
    async def test_can_disable_synthetic_turn_task_markers(self) -> None:
        events = [
            event_notification(
                "codex/event/task_started",
                {"taskId": "task-1", "task": {"title": "Inspect repository"}},
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(emit_turn_task_markers=False)
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        turn_marker_events = [
            event
            for event in tools.events_sent
            if "codex_turn_status" in event["metadata"]
        ]
        assert turn_marker_events == []
        assert any(
            event["metadata"].get("codex_event_method") == "codex/event/task_started"
            for event in tools.events_sent
        )

    @pytest.mark.asyncio
    async def test_raw_task_event_without_explicit_task_id_does_not_emit_uuid(
        self,
    ) -> None:
        events = [
            event_notification(
                "codex/event/task_started",
                {"id": "turn-1"},
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(emit_turn_task_markers=False)
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        raw_task_event = next(
            event
            for event in tools.events_sent
            if event["metadata"].get("codex_event_method") == "codex/event/task_started"
        )
        assert raw_task_event["content"] == (
            "Task: Codex task lifecycle event\nStatus: started\n"
            "Summary: Method: codex/event/task_started"
        )
        assert "codex_task_id" not in raw_task_event["metadata"]

    @pytest.mark.asyncio
    async def test_status_command_returns_state_without_starting_turn(self) -> None:
        fake_client = FakeCodexClient()
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(content="@thenvoi/ar-2-darter /status"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        methods = fake_client.request_methods
        assert CodexRequestMethod.TURN_START not in methods
        assert CodexRequestMethod.THREAD_START not in methods
        assert len(tools.messages_sent) == 1
        assert "Codex status:" in tools.messages_sent[0]["content"]
        assert "thread_id: not mapped" in tools.messages_sent[0]["content"]

    @pytest.mark.asyncio
    async def test_status_reports_the_room_model_without_registering_a_room(
        self, codex_room: Callable[..., Awaitable[CodexRoom]]
    ) -> None:
        room = await codex_room(
            tool_call_request(1, BandTool.NO_REPLY), turn_completed()
        )

        await room.send("/status")
        assert ROOM_ID not in room.adapter._room_clients
        await room.send("work")
        await room.send("/status")

        assert "selected_model: gpt-5.5\n" in room.chat[-1]

    @pytest.mark.asyncio
    async def test_model_command_sets_override_without_starting_turn(self) -> None:
        fake_client = FakeCodexClient()
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(content="/model gpt-5.5-codex"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        methods = fake_client.request_methods
        assert CodexRequestMethod.TURN_START not in methods
        assert CodexRequestMethod.THREAD_START not in methods
        assert adapter._selected_model == "gpt-5.5-codex"
        assert len(tools.messages_sent) == 1
        assert (
            "Model override set to `gpt-5.5-codex`" in tools.messages_sent[0]["content"]
        )

    @pytest.mark.asyncio
    async def test_models_alias_lists_models_without_starting_turn(self) -> None:
        fake_client = FakeCodexClient()
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(content="/models list"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        methods = fake_client.request_methods
        assert CodexRequestMethod.TURN_START not in methods
        assert CodexRequestMethod.THREAD_START not in methods
        assert methods.count(CodexRequestMethod.MODEL_LIST) >= 1
        assert len(tools.messages_sent) == 1
        assert "Available models" in tools.messages_sent[0]["content"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("effort", ["high", "max"])
    async def test_reasoning_effort_passed_in_turn_overrides(self, effort: str) -> None:
        """Efforts newer than any list the SDK could hard-code reach Codex as-is."""
        fake_client = FakeCodexClient(events=[turn_completed()])
        adapter = make_codex_adapter(
            fake_client,
            config=CodexAdapterConfig(
                reasoning_effort=effort, reasoning_summary="concise"
            ),
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(content="hello"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        turn_params = fake_client.params_of(CodexRequestMethod.TURN_START)[0]
        assert turn_params["effort"] == effort
        assert turn_params["summary"] == "concise"

    @pytest.mark.asyncio
    async def test_reasoning_effort_omitted_when_none(self) -> None:
        fake_client = FakeCodexClient(events=[turn_completed()])
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(content="hello"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        turn_params = fake_client.params_of(CodexRequestMethod.TURN_START)[0]
        assert "effort" not in turn_params
        assert "summary" not in turn_params

    @pytest.mark.asyncio
    @pytest.mark.parametrize("effort", ["high", "ultra"])
    async def test_reasoning_command_sets_effort(self, effort: str) -> None:
        fake_client = FakeCodexClient()
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(content=f"/reasoning {effort}"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        room = adapter._room_clients["room-1"]
        assert room.reasoning_effort == effort
        assert len(tools.messages_sent) == 1
        assert (
            f"Reasoning effort set to `{effort}`" in tools.messages_sent[0]["content"]
        )

    @pytest.mark.asyncio
    async def test_reasoning_command_lists_efforts_the_model_supports(self) -> None:
        fake_client = FakeCodexClient(model_list_result=LIVE_EFFORTS_MODEL_LIST)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(model="gpt-6-sol")
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(content="/reasoning"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        assert len(tools.messages_sent) == 1
        assert (
            "`gpt-6-sol` supports: low, max, ultra."
            in tools.messages_sent[0]["content"]
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("effort", "stored", "reply"),
        [
            ("ultra", "ultra", "Reasoning effort set to `ultra`"),
            (
                "hgih",
                None,
                (
                    "`gpt-6-sol` doesn't support reasoning effort `hgih`. "
                    "Supported: low, max, ultra."
                ),
            ),
        ],
    )
    async def test_reasoning_command_checks_the_models_live_efforts(
        self, effort: str, stored: str | None, reply: str
    ) -> None:
        """A typo is refused up front instead of failing every later turn."""
        fake_client = FakeCodexClient(model_list_result=LIVE_EFFORTS_MODEL_LIST)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(model="gpt-6-sol")
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(content=f"/reasoning {effort}"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        assert adapter._room_clients["room-1"].reasoning_effort == stored
        assert len(tools.messages_sent) == 1
        assert reply in tools.messages_sent[0]["content"]

    @pytest.mark.asyncio
    async def test_reasoning_command_still_answers_when_model_list_fails(self) -> None:
        fake_client = FakeCodexClient(
            model_list_error=RuntimeError("model/list unavailable")
        )
        adapter = make_codex_adapter(
            fake_client,
            config=CodexAdapterConfig(model="gpt-6-sol", reasoning_effort="high"),
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(content="/reasoning"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        assert len(tools.messages_sent) == 1
        reply = tools.messages_sent[0]["content"]
        assert "Current reasoning effort: `high`" in reply
        assert "supports:" not in reply

    @pytest.mark.asyncio
    async def test_self_config_tools_registered_when_enabled(self) -> None:
        fake_client = FakeCodexClient(events=[turn_completed()])
        adapter = make_codex_adapter(
            fake_client,
            config=CodexAdapterConfig(enable_self_config_tools=True),
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(content="hello"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        # Check that thread/start included setmodel and setreasoning dynamic tools
        thread_params = fake_client.params_of(CodexRequestMethod.THREAD_START)[0]
        tool_names = [t["name"] for t in thread_params.get("dynamicTools", [])]
        assert "setmodel" in tool_names
        assert "setreasoning" in tool_names

    @pytest.mark.asyncio
    async def test_self_config_tools_not_registered_when_disabled(self) -> None:
        fake_client = FakeCodexClient(events=[turn_completed()])
        adapter = make_codex_adapter(
            fake_client,
            config=CodexAdapterConfig(enable_self_config_tools=False),
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(content="hello"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        thread_params = fake_client.params_of(CodexRequestMethod.THREAD_START)[0]
        tool_names = [t["name"] for t in thread_params.get("dynamicTools", [])]
        assert "setmodel" not in tool_names
        assert "setreasoning" not in tool_names

    @pytest.mark.asyncio
    async def test_setmodel_tool_changes_model(self) -> None:
        events = [
            event_request(
                99,
                "item/tool/call",
                {
                    "tool": "setmodel",
                    "callId": "call-1",
                    "arguments": {"model": "o3"},
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(enable_self_config_tools=True)
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(content="switch to o3"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        assert adapter._selected_model == "o3"
        # Verify the tool response was sent back
        tool_responses = [
            (rid, result)
            for rid, result in fake_client.responses
            if isinstance(result, dict) and "contentItems" in result
        ]
        assert len(tool_responses) >= 1
        result_text = tool_responses[0][1]["contentItems"][0]["text"]
        assert "o3" in result_text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("effort", ["xhigh", "ultra"])
    async def test_setreasoning_tool_changes_effort(self, effort: str) -> None:
        events = [
            event_request(
                99,
                "item/tool/call",
                {
                    "tool": "setreasoning",
                    "callId": "call-2",
                    "arguments": {"effort": effort, "summary": "detailed"},
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(enable_self_config_tools=True)
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(content="increase reasoning"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        room = adapter._room_clients["room-1"]
        assert room.reasoning_effort == effort
        assert room.reasoning_summary == "detailed"

    @pytest.mark.asyncio
    async def test_sandbox_alias_is_normalized_for_thread_and_turn(self) -> None:
        events = [turn_completed()]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(sandbox="dangerFullAccess")
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        thread_start = fake_client.params_of(CodexRequestMethod.THREAD_START)[0]
        turn_start = fake_client.params_of(CodexRequestMethod.TURN_START)[0]
        # thread/start only accepts the sandbox field (SandboxMode enum)
        assert thread_start["sandbox"] == "danger-full-access"
        # turn/start uses sandboxPolicy (full SandboxPolicy tagged union)
        assert turn_start["sandboxPolicy"]["type"] == "dangerFullAccess"

    @pytest.mark.asyncio
    async def test_external_sandbox_alias_uses_sandbox_policy(self) -> None:
        events = [turn_completed()]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(sandbox="external-sandbox")
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        thread_start = fake_client.params_of(CodexRequestMethod.THREAD_START)[0]
        turn_start = fake_client.params_of(CodexRequestMethod.TURN_START)[0]
        # thread/start has no sandboxPolicy field; externalSandbox is
        # only representable at turn level
        assert "sandbox" not in thread_start
        assert "sandboxPolicy" not in thread_start
        # turn/start can express the full SandboxPolicy tagged union
        assert turn_start["sandboxPolicy"]["type"] == "externalSandbox"

    @pytest.mark.asyncio
    async def test_transport_closed_event_aborts_turn(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A transport/closed event ends the turn with a failure the room sees
        and a WARNING the operator sees (the runtime logs it only at DEBUG)."""
        events = [
            event_notification(
                "transport/closed",
                {"reason": "Codex process exited unexpectedly"},
            )
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        with pytest.raises(TurnResultAlreadyReported):
            await deliver_message(
                adapter,
                make_platform_message(),
                tools,
                CodexSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )

        # Adapter should report a failure mentioning the disconnect.
        failures = reported_failures(tools)
        assert any("transport closed" in f["message"].lower() for f in failures)
        (warning,) = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert "Codex process exited unexpectedly" in warning.getMessage()

    @pytest.mark.asyncio
    async def test_a_lost_process_is_retired_and_its_thread_restored_exactly(
        self,
    ) -> None:
        """Recovery closes a lost process but keeps the room's thread, usage
        and settings: the next turn resumes exactly that thread on a new
        process, sending only overrides resume supports, and never starts a
        fresh one in its place."""
        client = FakeCodexClient(
            events=[
                event_notification(TRANSPORT_CLOSED, {"reason": "process exited"}),
                turn_completed("turn-2"),
            ]
        )
        adapter = make_codex_adapter(client)
        await adapter.on_started("Codex Agent", "A coding agent")
        with pytest.raises(TurnResultAlreadyReported):
            await send_bootstrap(adapter)
        thread_id = adapter._room_threads[ROOM_ID]
        usage = adapter._token_usage[thread_id] = CodexTokenUsage(total_tokens=150)

        await send_bootstrap(adapter)

        assert client.closed
        assert client.request_methods.count(CodexRequestMethod.THREAD_START) == 1
        (restore,) = client.params_of(CodexRequestMethod.THREAD_RESUME)
        assert restore["threadId"] == thread_id
        assert set(restore) == {"threadId", "cwd", "personality"}
        assert adapter._room_threads[ROOM_ID] == thread_id
        assert adapter._token_usage[thread_id] is usage

    @pytest.mark.asyncio
    async def test_a_thread_resumed_from_history_survives_a_lost_process(
        self,
    ) -> None:
        """A thread Band history names was recorded in another cwd; resuming
        it pins the room's workspace, so a later strict restore agrees."""
        client = FakeCodexClient(
            events=[
                event_notification(TRANSPORT_CLOSED, {"reason": "process exited"}),
                turn_completed("turn-2"),
            ],
            recorded_cwd="/where/it/first/ran",
        )
        adapter = make_codex_adapter(client)
        await adapter.on_started("Codex Agent", "A coding agent")
        with pytest.raises(TurnResultAlreadyReported):
            await send_bootstrap(
                adapter, history=CodexSessionState(thread_id="thr-history")
            )

        await send_bootstrap(adapter)

        assert client.request_methods.count(CodexRequestMethod.THREAD_RESUME) == 2
        assert CodexRequestMethod.THREAD_START not in client.request_methods
        assert adapter._room_threads[ROOM_ID] == "thr-history"

    @pytest.mark.asyncio
    async def test_transport_closed_after_error_does_not_double_report(self) -> None:
        """An "error" notification immediately followed by transport/closed for
        the same incident must report the failure once, not twice -- matching
        the turn/completed branch's existing failure_reported guard."""
        events = [
            event_notification(
                "error",
                {"error": {"message": "Something went wrong"}, "willRetry": False},
            ),
            event_notification(
                "transport/closed",
                {"reason": "Codex process exited unexpectedly"},
            ),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Codex Agent", "A coding agent")

        with pytest.raises(TurnResultAlreadyReported):
            await deliver_message(
                adapter,
                make_platform_message(),
                tools,
                CodexSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )

        assert len(reported_failures(tools)) == 1

    @pytest.mark.asyncio
    async def test_turn_timeout_sends_interrupt_and_clean_error(self) -> None:
        """When recv_event times out, the adapter sends turn/interrupt, reports
        the failure, and fails the turn so its delivery is marked FAILED --
        same as every sibling adapter's own turn-timeout handling."""
        # No events means FakeCodexClient raises asyncio.TimeoutError immediately.
        fake_client = FakeCodexClient(events=[])
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(turn_timeout_s=0.01)
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        with pytest.raises(TurnResultAlreadyReported):
            await deliver_message(
                adapter,
                make_platform_message(),
                tools,
                CodexSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )

        # Adapter should have sent turn/interrupt with both identifiers.
        assert fake_client.params_of(CodexRequestMethod.TURN_INTERRUPT) == [
            {"threadId": "thr-1", "turnId": "turn-1"}
        ]

        # The turn fails the platform's turn, so no separate "I stopped..."
        # chat reply goes out alongside the structured failure event.
        assert not tools.messages_sent

        failures = reported_failures(tools)
        assert len(failures) == 1
        assert failures[0]["provider"] == "codex"
        assert failures[0]["code"] == "timeout"

    @pytest.mark.asyncio
    async def test_turn_timeout_emits_failed_lifecycle_event(self) -> None:
        """A timed-out turn still gets a failed-status lifecycle event, same
        as the transport/closed and turn/completed(status=failed) paths --
        the timeout branch's TurnResultAlreadyReported raise happens from
        inside its own except clause, so it can't rely on the sibling
        `except TurnResultAlreadyReported` handler to emit it."""
        fake_client = FakeCodexClient(events=[])
        adapter = make_codex_adapter(
            fake_client,
            config=CodexAdapterConfig(
                turn_timeout_s=0.01,
                emit_turn_lifecycle_events=True,
            ),
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        with pytest.raises(TurnResultAlreadyReported):
            await deliver_message(
                adapter,
                make_platform_message(),
                tools,
                CodexSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )

        lifecycle_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_event_type") == "turn_lifecycle"
        ]
        assert len(lifecycle_events) == 2
        assert lifecycle_events[0]["metadata"]["codex_turn_status"] == "started"
        assert lifecycle_events[1]["metadata"]["codex_turn_status"] == "failed"

    @pytest.mark.asyncio
    async def test_item_completed_text_overrides_accumulated_deltas(self) -> None:
        """item/completed text is authoritative and should replace any accumulated deltas."""
        events = [
            event_notification(
                "item/agentMessage/delta",
                {"itemId": "msg-1", "delta": "partial "},
            ),
            event_notification(
                "item/agentMessage/delta",
                {"itemId": "msg-1", "delta": "garbled"},
            ),
            event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "agentMessage",
                        "id": "msg-1",
                        "text": "authoritative final text",
                    }
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # The authoritative text from item/completed should be used, not the deltas.
        assert tools.messages_sent == []
        assert [e["content"] for e in events_of_type(tools, "thought")] == [
            "authoritative final text"
        ]

    @pytest.mark.asyncio
    async def test_custom_tools_schemas_merged_into_dynamic_tools(self) -> None:
        """Custom tool schemas appear in _build_dynamic_tools output."""

        class WeatherInput(BaseModel):
            """Get current weather for a location."""

            city: str

        def get_weather(inp: WeatherInput) -> str:
            return f"Sunny in {inp.city}"

        custom_tools: list[CustomToolDef] = [(WeatherInput, get_weather)]
        adapter = CodexAdapter(
            config=CodexAdapterConfig(),
            additional_tools=custom_tools,
        )

        tools = ToolSchemaFakeTools()
        dynamic_tools = adapter._build_dynamic_tools(tools)

        names = [t["name"] for t in dynamic_tools]
        assert "weather" in names

        weather_tool = next(t for t in dynamic_tools if t["name"] == "weather")
        assert weather_tool["description"] == "Get current weather for a location."
        assert "inputSchema" in weather_tool
        assert "city" in weather_tool["inputSchema"].get("properties", {})

    @pytest.mark.asyncio
    async def test_custom_tool_dispatched_before_platform_tools(self) -> None:
        """Custom tool is invoked via execute_custom_tool, not platform tools."""

        class CalculatorInput(BaseModel):
            """Simple calculator."""

            expression: str

        call_log: list[str] = []

        async def calculate(inp: CalculatorInput) -> str:
            call_log.append(inp.expression)
            return "42"

        custom_tools: list[CustomToolDef] = [(CalculatorInput, calculate)]
        events = [
            event_request(
                99,
                "item/tool/call",
                {
                    "tool": "calculator",
                    "arguments": {"expression": "6*7"},
                    "callId": "call-99",
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(), additional_tools=custom_tools
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # Custom tool was called
        assert call_log == ["6*7"]
        # Platform execute_tool_call was NOT called for the custom tool
        assert not any(tc["tool_name"] == "calculator" for tc in tools.tool_calls)
        # Response was sent back to Codex
        assert fake_client.responses
        _, payload = fake_client.responses[0]
        assert payload["success"] is True
        assert payload["contentItems"][0]["text"] == "42"

    @pytest.mark.asyncio
    async def test_execution_reporting_emits_tool_call_and_result_events(self) -> None:
        """With emit=Emit.TOOL_CALLS, tool_call and tool_result events are emitted."""
        events = [
            event_request(
                50,
                "item/tool/call",
                {
                    "tool": "band_lookup_peers",
                    "arguments": {"page": 1},
                    "callId": "call-50",
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(), emit=Emit.TOOL_CALLS
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        tool_call_events = events_of_type(tools, "tool_call")
        tool_result_events = events_of_type(tools, "tool_result")
        assert len(tool_call_events) == 1
        assert len(tool_result_events) == 1

        call_data = json.loads(tool_call_events[0]["content"])
        assert call_data["name"] == "band_lookup_peers"
        assert call_data["tool_call_id"] == "call-50"

        result_data = json.loads(tool_result_events[0]["content"])
        assert result_data["name"] == "band_lookup_peers"
        assert result_data["tool_call_id"] == "call-50"

    @pytest.mark.asyncio
    async def test_send_room_file_tool_call_event_redacts_content(self) -> None:
        """band_send_room_file's raw content must never reach a tool_call
        event -- report has no idea content can carry real file bytes."""
        raw_content = "raw file bytes that must never reach a tool_call event"
        events = [
            tool_call_request(
                50, "band_send_room_file", {"content": raw_content, "filename": "f.txt"}
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(), emit=Emit.TOOL_CALLS
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        call_data = json.loads(events_of_type(tools, "tool_call")[0]["content"])
        assert (
            call_data["args"]["content"]
            == f"<{len(raw_content.encode('utf-8'))} byte file content>"
        )
        assert raw_content not in json.dumps(call_data)

    @pytest.mark.asyncio
    async def test_execution_reporting_silenced_with_explicit_empty_emit(self) -> None:
        """emit=() silences tool_call/tool_result events (emit otherwise defaults on)."""
        events = [
            event_request(
                50,
                "item/tool/call",
                {
                    "tool": "band_lookup_peers",
                    "arguments": {"page": 1},
                    "callId": "call-50",
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig(), emit=())
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        tool_events = [
            e
            for e in tools.events_sent
            if e["message_type"] in {"tool_call", "tool_result"}
        ]
        assert tool_events == []

    @pytest.mark.asyncio
    async def test_execution_reporting_on_tool_error(self) -> None:
        """Execution reporting emits tool_result with error text on failure."""

        class FailInput(BaseModel):
            """A tool that always fails."""

            x: int

        async def fail_func(inp: FailInput) -> str:
            raise RuntimeError("boom")

        custom_tools: list[CustomToolDef] = [(FailInput, fail_func)]
        events = [
            event_request(
                60,
                "item/tool/call",
                {
                    "tool": "fail",
                    "arguments": {"x": 1},
                    "callId": "call-60",
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client,
            config=CodexAdapterConfig(),
            additional_tools=custom_tools,
            emit=Emit.TOOL_CALLS,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        tool_result_events = events_of_type(tools, "tool_result")
        assert len(tool_result_events) == 1
        result_data = json.loads(tool_result_events[0]["content"])
        assert result_data["name"] == "fail"
        assert "boom" in result_data["output"]
        assert result_data["tool_call_id"] == "call-60"

        # Codex response should indicate failure
        _, payload = fake_client.responses[0]
        assert payload["success"] is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "tool_name",
        ["band_send_event", "band_send_message"],
    )
    async def test_execution_reporting_emitted_for_platform_output_tools(
        self, tool_name: str
    ) -> None:
        """Band messaging tools are reported like any other tool — no suppression."""
        events = [
            event_request(
                70,
                "item/tool/call",
                {
                    "tool": tool_name,
                    "arguments": {"content": "test", "message_type": "thought"},
                    "callId": "call-70",
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(), emit=Emit.TOOL_CALLS
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # The tool call itself should still execute
        assert len(tools.tool_calls) == 1
        assert tools.tool_calls[0]["tool_name"] == tool_name

        # And it's reported like any other tool call
        reporting_events = [
            e
            for e in tools.events_sent
            if e["message_type"] in {"tool_call", "tool_result"}
        ]
        assert [e["message_type"] for e in reporting_events] == [
            "tool_call",
            "tool_result",
        ]
