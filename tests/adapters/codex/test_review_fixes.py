"""Codex adapter: tests for issues identified in PR review."""

from __future__ import annotations

import pytest

from band.adapters.codex import (
    CodexAdapter,
    CodexAdapterConfig,
)
from band.core.protocols import (
    GENERIC_PROVIDER_FAILURE_MESSAGE,
    TurnResultAlreadyReported,
)
from band.integrations.codex import CodexJsonRpcError, RpcEvent
from band.integrations.codex.types import (
    CodexSessionState,
    CodexTokenUsage,
)
from band.testing import (
    events_of_type,
    reported_failures,
)
from tests.adapters.codex.kit import (
    ToolSchemaFakeTools,
    make_platform_message,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    event_notification,
    make_codex_adapter,
    turn_completed,
)
from tests.adapters.dispatch import deliver_message


class TestReviewFixes:
    """Tests for issues identified in PR review."""

    @pytest.mark.asyncio
    async def test_sandbox_command_blocked_when_sandbox_policy_set(self) -> None:
        """/sandbox is rejected when sandbox_policy is configured."""
        fake_client = FakeCodexClient()
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(sandbox_policy={"type": "readOnly"})
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(content="/sandbox workspace-write"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert "room-1" not in adapter._sandbox_overrides
        assert "Cannot override sandbox" in tools.messages_sent[0]["content"]

    @pytest.mark.asyncio
    async def test_thread_archive_clears_raw_history(self) -> None:
        """/thread archive also clears raw history and injection state."""
        events = [
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
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

        # Seed raw history and injection flag
        adapter._raw_history_by_room["room-1"] = [{"role": "user", "content": "hi"}]
        adapter._needs_history_injection.add("room-1")

        await deliver_message(
            adapter,
            make_platform_message(content="/thread archive"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        assert "room-1" not in adapter._raw_history_by_room
        assert "room-1" not in adapter._needs_history_injection

    def test_token_usage_update_handles_zero_values(self) -> None:
        """CodexTokenUsage.update() correctly handles explicit zero values."""

        usage = CodexTokenUsage()
        usage.update(
            {
                "usage": {
                    "inputTokens": 0,
                    "outputTokens": 100,
                    "reasoningTokens": 0,
                    "totalTokens": 100,
                }
            }
        )
        assert usage.input_tokens == 0
        assert usage.output_tokens == 100
        assert usage.reasoning_tokens == 0
        assert usage.total_tokens == 100

    def test_session_approval_key_empty_prevents_wildcard_match(self) -> None:
        """Empty session key from missing command cannot match any session set."""
        adapter = CodexAdapter(config=CodexAdapterConfig())
        key = adapter._session_approval_key("item/commandExecution/requestApproval", {})
        # Empty key is falsy, so `key and key in session_set` is always False
        assert not key
        assert not (key and key in {"commandExecution:npm"})

    @pytest.mark.asyncio
    async def test_unexpected_recv_error_reports_and_fails_turn(self) -> None:
        """When the event stream breaks mid-turn, the adapter reports an
        AgentFailure and fails the turn instead of silently degrading to a
        plain chat reply with no structured signal at all."""

        class BrokenClient(FakeCodexClient):
            async def recv_event(self, timeout_s: float | None = None) -> RpcEvent:
                await self.turn_started.wait()
                raise ConnectionError("transport died")

        fake_client = BrokenClient()
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
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
        failures = reported_failures(tools)
        assert len(failures) == 1
        assert failures[0]["provider"] == "codex"
        assert failures[0]["message"] == GENERIC_PROVIDER_FAILURE_MESSAGE

    @pytest.mark.asyncio
    async def test_rpc_error_from_event_loop_keeps_curated_failure(self) -> None:
        """RPC errors raised while receiving events keep their provider message."""
        rpc_error = CodexJsonRpcError(code=-32000, message="model unavailable")

        class RpcErrorClient(FakeCodexClient):
            async def recv_event(self, timeout_s: float | None = None) -> RpcEvent:
                await self.turn_started.wait()
                raise rpc_error

        rpc_error_client = RpcErrorClient()
        adapter = make_codex_adapter(rpc_error_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")

        with pytest.raises(CodexJsonRpcError, match="model unavailable"):
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

        failures = reported_failures(tools)
        assert len(failures) == 1
        assert failures[0]["message"] == str(rpc_error)

    @pytest.mark.asyncio
    async def test_failed_turn_emits_terminal_lifecycle_event(self) -> None:
        """A reported failed turn still closes the lifecycle event pair."""
        events = [
            event_notification(
                "turn/completed",
                {"turn": {"id": "turn-1", "status": "failed", "items": []}},
            )
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client,
            config=CodexAdapterConfig(
                emit_turn_lifecycle_events=True,
            ),
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")

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
            event
            for event in events_of_type(tools, "task")
            if event["metadata"].get("codex_event_type") == "turn_lifecycle"
        ]
        assert [
            event["metadata"]["codex_turn_status"] for event in lifecycle_events
        ] == ["started", "failed"]
