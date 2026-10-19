"""Codex adapter: Codex task events are forwarded as Band task events."""

from __future__ import annotations

import pytest

from band.adapters.codex import (
    CodexAdapterConfig,
)
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


class TestRawTaskEvents:
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
        await send_bootstrap(adapter, tools=tools)

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
        await send_bootstrap(adapter, tools=tools)

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
        await send_bootstrap(adapter, tools=tools)

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
