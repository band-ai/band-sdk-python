"""Codex adapter: a turn's start and terminal outcome are emitted as lifecycle events."""

from __future__ import annotations

import pytest

from band.adapters.codex import CodexAdapterConfig
from band.core.protocols import TurnResultAlreadyReported
from band.integrations.codex.types import CodexSessionState
from band.testing import events_of_type
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


class TestTurnLifecycleEvents:
    @pytest.mark.asyncio
    async def test_turn_started_lifecycle_event_emitted(self) -> None:
        """Turn started lifecycle event includes input summary."""
        events = [
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(emit_turn_lifecycle_events=True)
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(content="fix the login bug"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        started_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_event_type") == "turn_lifecycle"
            and e["metadata"].get("codex_turn_status") == "started"
        ]
        assert len(started_events) == 1
        assert (
            started_events[0]["metadata"]["codex_input_summary"] == "fix the login bug"
        )

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
            await send_bootstrap(adapter, tools=tools)

        lifecycle_events = [
            event
            for event in events_of_type(tools, "task")
            if event["metadata"].get("codex_event_type") == "turn_lifecycle"
        ]
        assert [
            event["metadata"]["codex_turn_status"] for event in lifecycle_events
        ] == ["started", "failed"]
