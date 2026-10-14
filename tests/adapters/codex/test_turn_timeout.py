"""Codex adapter: a turn past its deadline is interrupted and reported."""

from __future__ import annotations

import pytest

from band.adapters.codex import (
    CodexAdapterConfig,
)
from band.core.protocols import FAILURE_CODE_TIMEOUT, TurnResultAlreadyReported
from band.integrations.codex.types import (
    CodexRequestMethod,
)
from band.testing import (
    reported_failures,
)
from tests.adapters.codex.kit import (
    ToolSchemaFakeTools,
    send_bootstrap,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    make_codex_adapter,
)


class TestTurnTimeout:
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
            await send_bootstrap(adapter, tools=tools)

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
            await send_bootstrap(adapter, tools=tools)

        lifecycle_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_event_type") == "turn_lifecycle"
        ]
        assert len(lifecycle_events) == 2
        assert lifecycle_events[0]["metadata"]["codex_turn_status"] == "started"
        assert lifecycle_events[1]["metadata"]["codex_turn_status"] == "failed"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "interrupt_error",
        [None, ConnectionResetError("Codex process is gone")],
        ids=["interrupted", "interrupt-failed"],
    )
    async def test_a_timed_out_turn_is_reported_once_and_the_room_serves_on(
        self, interrupt_error: Exception | None
    ) -> None:
        """Whether or not Codex takes the interrupt, the timeout is the turn's
        one failure and the room's next message still reaches Codex."""
        client = FakeCodexClient(events=[], interrupt_error=interrupt_error)
        adapter = make_codex_adapter(
            client, config=CodexAdapterConfig(turn_timeout_s=0.01)
        )
        deliveries = [ToolSchemaFakeTools(), ToolSchemaFakeTools()]
        await adapter.on_started("Codex Agent", "A coding agent")

        for tools in deliveries:
            with pytest.raises(TurnResultAlreadyReported):
                await send_bootstrap(adapter, tools=tools)

        assert [
            [failure["code"] for failure in reported_failures(tools)]
            for tools in deliveries
        ] == [[FAILURE_CODE_TIMEOUT], [FAILURE_CODE_TIMEOUT]]
        assert len(client.params_of(CodexRequestMethod.TURN_START)) == 2
