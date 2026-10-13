"""Codex adapter: a lost Codex process fails its turn once and the room recovers onto the same thread."""

from __future__ import annotations

import logging

import pytest

from band.adapters.codex import (
    CodexAdapterConfig,
)
from band.core.protocols import (
    GENERIC_PROVIDER_FAILURE_MESSAGE,
    TurnResultAlreadyReported,
)
from band.integrations.codex import TRANSPORT_CLOSED, RpcEvent
from band.integrations.codex.types import (
    CodexRequestMethod,
    CodexSessionState,
    CodexTokenUsage,
)
from band.testing import (
    reported_failures,
)
from tests.adapters.codex.kit import (
    PROCESS_EXITED,
    ROOM_ID,
    ToolSchemaFakeTools,
    send_bootstrap,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    event_notification,
    make_codex_adapter,
    turn_completed,
)


class TestProcessLossRecovery:
    @pytest.mark.asyncio
    async def test_transport_closed_event_aborts_turn(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A transport/closed event ends the turn with a failure the room sees
        and a WARNING the operator sees (the runtime logs it only at DEBUG)."""
        events = [
            event_notification(
                TRANSPORT_CLOSED,
                {"reason": "Codex process exited unexpectedly"},
            )
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        with pytest.raises(TurnResultAlreadyReported):
            await send_bootstrap(adapter, tools=tools)

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
                PROCESS_EXITED,
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
                PROCESS_EXITED,
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
                TRANSPORT_CLOSED,
                {"reason": "Codex process exited unexpectedly"},
            ),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Codex Agent", "A coding agent")

        with pytest.raises(TurnResultAlreadyReported):
            await send_bootstrap(adapter, tools=tools)

        assert len(reported_failures(tools)) == 1

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
            await send_bootstrap(adapter, tools=tools)
        failures = reported_failures(tools)
        assert len(failures) == 1
        assert failures[0]["provider"] == "codex"
        assert failures[0]["message"] == GENERIC_PROVIDER_FAILURE_MESSAGE
