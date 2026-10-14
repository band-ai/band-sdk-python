"""Codex adapter: context compaction."""

from __future__ import annotations

import pytest

from band.adapters.codex import CodexAdapterConfig
from tests.adapters.codex.kit import (
    ToolSchemaFakeTools,
    send_bootstrap,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    event_notification,
    make_codex_adapter,
    turn_completed,
)


class TestContextCompaction:
    @pytest.mark.asyncio
    async def test_context_compaction_event_emitted(self) -> None:
        """context/compacted events are forwarded as task events."""
        events = [
            event_notification(
                "context/compacted",
                {"threadId": "thr-1", "turnId": "turn-1"},
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(emit_turn_lifecycle_events=True)
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        compaction_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_event_type") == "context_compaction"
        ]
        assert len(compaction_events) == 1
        assert compaction_events[0]["metadata"]["codex_thread_id"] == "thr-1"

    @pytest.mark.asyncio
    async def test_context_compaction_ignored_when_disabled(self) -> None:
        """Compaction events are not emitted when emit_turn_lifecycle_events=False."""
        events = [
            event_notification(
                "context/compacted",
                {"threadId": "thr-1", "turnId": "turn-1"},
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(emit_turn_lifecycle_events=False)
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        compaction_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_event_type") == "context_compaction"
        ]
        assert compaction_events == []
