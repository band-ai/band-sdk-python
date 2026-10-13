"""Codex adapter: a room message starts or resumes the thread, runs a turn and settles its reply."""

from __future__ import annotations

from typing import Any

import pytest

from band.adapters.codex import (
    CodexAdapterConfig,
)
from band.integrations.codex import CodexJsonRpcError
from band.integrations.codex.types import (
    CodexRequestMethod,
    CodexSessionState,
)
from band.runtime.tools import ToolCallOutcome
from band.testing import (
    events_of_type,
)
from tests.adapters.codex.kit import (
    ROOM_ID,
    ToolSchemaFakeTools,
    make_platform_message,
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


class TestTurnDelivery:
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
        await send_bootstrap(adapter, tools=tools)

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
        await send_bootstrap(adapter, tools=tools)

        assert tools.messages_sent == []
        thoughts = [e for e in tools.events_sent if e["message_type"] == "thought"]
        assert [e["content"] for e in thoughts] == ["(Waiting on the review.)"]
        assert not tools.turn.complete

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
                make_platform_message(room_id=ROOM_ID, content="first try"),
                tools,
                CodexSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id=ROOM_ID,
            )
        assert ROOM_ID not in adapter._prompt_injected_rooms

        await deliver_message(
            adapter,
            make_platform_message(room_id=ROOM_ID, content="second try"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id=ROOM_ID,
        )
        assert ROOM_ID in adapter._prompt_injected_rooms

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
        await send_bootstrap(adapter, tools=tools)

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
        await send_bootstrap(adapter, tools=tools)

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
            CodexSessionState(thread_id="thr-old", room_id=ROOM_ID),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id=ROOM_ID,
        )

        methods = fake_client.request_methods
        assert CodexRequestMethod.THREAD_RESUME in methods
        assert CodexRequestMethod.THREAD_START in methods

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
        await send_bootstrap(adapter, tools=tools)

        # The authoritative text from item/completed should be used, not the deltas.
        assert tools.messages_sent == []
        assert [e["content"] for e in events_of_type(tools, "thought")] == [
            "authoritative final text"
        ]
