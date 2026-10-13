"""Codex adapter: structured errors."""

from __future__ import annotations

import pytest

from band.adapters.codex import CodexAdapterConfig
from band.core.protocols import (
    GENERIC_PROVIDER_FAILURE_MESSAGE,
    TurnResultAlreadyReported,
)
from band.integrations.codex import CodexJsonRpcError, RpcEvent
from band.integrations.codex.types import CodexSessionState
from band.testing import reported_failures
from tests.adapters.codex.kit import (
    ToolSchemaFakeTools,
    make_platform_message,
    send_bootstrap,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    event_notification,
    make_codex_adapter,
    turn_completed,
)
from tests.adapters.dispatch import deliver_message


class TestStructuredErrors:
    @pytest.mark.asyncio
    async def test_structured_error_from_error_event(self) -> None:
        """Error events with codexErrorInfo emit structured metadata."""
        events = [
            event_notification(
                "error",
                {
                    "error": {
                        "message": "Context window exceeded",
                        "codexErrorInfo": {
                            "type": "ContextWindowExceeded",
                            "code": "context_window_exceeded",
                            "retryable": False,
                        },
                    },
                    "willRetry": False,
                },
            ),
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

        failures = reported_failures(tools)
        assert len(failures) == 1
        assert failures[0]["provider"] == "codex"
        assert failures[0]["code"] == "ContextWindowExceeded"
        assert failures[0]["detail"]["codex_is_retryable"] is False
        assert "context window" in failures[0]["message"].lower()

    @pytest.mark.asyncio
    async def test_structured_error_from_failed_turn(self) -> None:
        """turn/completed with status=failed and codexErrorInfo emits structured error."""
        events = [
            event_notification(
                "turn/completed",
                {
                    "turn": {
                        "id": "turn-1",
                        "status": "failed",
                        "error": {
                            "message": "Usage limit hit",
                            "codexErrorInfo": {
                                "type": "UsageLimitExceeded",
                                "code": "usage_limit",
                                "retryable": False,
                            },
                        },
                    }
                },
            ),
        ]
        fake_client = FakeCodexClient(events=events)
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
        assert failures[0]["code"] == "UsageLimitExceeded"

    @pytest.mark.asyncio
    async def test_structured_error_from_failed_turn_with_no_error_key(self) -> None:
        """turn/completed with status=failed but no "error" key at all must
        still report a failure before raising, not just claim it did."""
        events = [
            event_notification(
                "turn/completed",
                {"turn": {"id": "turn-1", "status": "failed", "items": []}},
            ),
        ]
        fake_client = FakeCodexClient(events=events)
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

    @pytest.mark.asyncio
    async def test_generic_exception_reports_and_propagates(self) -> None:
        """A bare exception outside Codex's structured-error paths (not a
        CodexJsonRpcError, not a delivery/already-reported failure) must
        still surface via the generic fallback and propagate."""
        fake_client = FakeCodexClient(
            events=[],
            turn_start_error=RuntimeError("transport hiccup"),
            turn_start_error_once=False,
        )
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        with pytest.raises(RuntimeError, match="transport hiccup"):
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
        assert "transport hiccup" not in failures[0]["message"]

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
            await send_bootstrap(adapter, tools=tools)

        failures = reported_failures(tools)
        assert len(failures) == 1
        assert failures[0]["message"] == str(rpc_error)
