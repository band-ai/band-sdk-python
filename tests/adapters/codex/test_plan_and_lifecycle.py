"""Codex adapter: plan and lifecycle."""

from __future__ import annotations

import pytest

from band.adapters.codex import CodexAdapterConfig
from band.integrations.codex.types import CodexSessionState
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


class TestPlanAndLifecycle:
    @pytest.mark.asyncio
    async def test_plan_steps_forwarded(self) -> None:
        """turn/plan/updated forwards structured plan steps."""
        events = [
            event_notification(
                "turn/plan/updated",
                {
                    "plan": {
                        "steps": [
                            {"text": "Read the failing test", "status": "completed"},
                            {"text": "Identify root cause", "status": "inProgress"},
                            {"text": "Apply fix", "status": "pending"},
                        ]
                    }
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(stream_plan_events=True)
        )
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

        plan_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_plan_steps") is not None
        ]
        assert len(plan_events) == 1
        steps = plan_events[0]["metadata"]["codex_plan_steps"]
        assert len(steps) == 3
        assert steps[0]["step"] == "Read the failing test"
        assert steps[0]["status"] == "completed"
        assert steps[2]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_plan_steps_not_forwarded_when_disabled(self) -> None:
        """turn/plan/updated is ignored when stream_plan_events=False."""
        events = [
            event_notification(
                "turn/plan/updated",
                {
                    "plan": {
                        "steps": [
                            {"text": "Step 1", "status": "pending"},
                        ]
                    }
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(stream_plan_events=False)
        )
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

        plan_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_plan_steps") is not None
        ]
        assert plan_events == []

    @pytest.mark.asyncio
    async def test_turn_lifecycle_events_emitted(self) -> None:
        """Enriched turn lifecycle events include duration and status."""
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
        # First event: turn started (with input summary)
        assert lifecycle_events[0]["metadata"]["codex_turn_status"] == "started"
        assert "codex_input_summary" in lifecycle_events[0]["metadata"]
        # Second event: turn completed (with duration)
        assert lifecycle_events[1]["metadata"]["codex_turn_status"] == "completed"
        assert "codex_duration_s" in lifecycle_events[1]["metadata"]

    @pytest.mark.asyncio
    async def test_threads_command_lists_mappings(self) -> None:
        """/threads command shows room→thread mappings."""
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

        # Now run /threads
        await deliver_message(
            adapter,
            make_platform_message(content="/threads"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        threads_msgs = [
            m
            for m in tools.messages_sent
            if "thread mappings" in m["content"].lower()
            or "active thread" in m["content"].lower()
        ]
        assert len(threads_msgs) >= 1
        assert "room-1" in threads_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_thread_archive_clears_mapping(self) -> None:
        """/thread archive removes the thread mapping."""
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
        assert "room-1" in adapter._room_threads

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
        assert "room-1" not in adapter._room_threads
        assert any("archived" in m["content"].lower() for m in tools.messages_sent)
