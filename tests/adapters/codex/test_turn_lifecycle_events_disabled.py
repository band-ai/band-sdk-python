"""Codex adapter: turn lifecycle events disabled."""

from __future__ import annotations

import pytest

from band.adapters.codex import CodexAdapterConfig
from tests.adapters.codex.kit import (
    ToolSchemaFakeTools,
    send_bootstrap,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    make_codex_adapter,
    turn_completed,
)


class TestTurnLifecycleEventsDisabled:
    @pytest.mark.asyncio
    async def test_no_lifecycle_events_when_disabled(self) -> None:
        """With emit_turn_lifecycle_events=False, neither started nor completed
        lifecycle task events are emitted."""
        events = [
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(emit_turn_lifecycle_events=False)
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")

        await send_bootstrap(adapter, tools=tools)

        lifecycle_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_event_type") == "turn_lifecycle"
        ]
        assert lifecycle_events == []
