"""Codex adapter: diffs and token usage."""

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


class TestDiffsAndTokenUsage:
    @pytest.mark.asyncio
    async def test_diff_event_forwarded(self) -> None:
        """turn/diff/updated forwards as a task event when enabled."""
        events = [
            event_notification(
                "turn/diff/updated",
                {
                    "diff": "--- a/src/app.py\n+++ b/src/app.py\n@@ ...",
                    "files": ["src/app.py"],
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(emit_diff_events=True)
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

        diff_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_event_type") == "turn_diff"
        ]
        assert len(diff_events) == 1
        assert diff_events[0]["message_type"] == "task"
        assert diff_events[0]["metadata"]["codex_files_changed"] == ["src/app.py"]
        assert "src/app.py" in diff_events[0]["metadata"]["codex_diff"]
        assert "1 files changed" in diff_events[0]["content"]

    @pytest.mark.asyncio
    async def test_diff_event_requires_task_events_emit(self) -> None:
        """Diffs are not forwarded when TASK_EVENTS is not in features.emit."""
        events = [
            event_notification(
                "turn/diff/updated",
                {"diff": "some diff", "files": ["f.py"]},
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(emit_diff_events=True), emit=()
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

        diff_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_event_type") == "turn_diff"
        ]
        assert diff_events == []

    @pytest.mark.asyncio
    async def test_token_usage_tracked_and_emitted(self) -> None:
        """thread/tokenUsage/updated events are tracked and emitted."""
        events = [
            event_notification(
                "thread/tokenUsage/updated",
                {
                    "usage": {
                        "inputTokens": 15000,
                        "outputTokens": 3200,
                        "reasoningTokens": 8000,
                        "totalTokens": 26200,
                    }
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(emit_token_usage_events=True)
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

        usage_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_event_type") == "token_usage"
        ]
        assert len(usage_events) == 1
        assert usage_events[0]["metadata"]["codex_input_tokens"] == 15000
        assert usage_events[0]["metadata"]["codex_output_tokens"] == 3200
        assert usage_events[0]["metadata"]["codex_total_tokens"] == 26200

    @pytest.mark.asyncio
    async def test_token_usage_ignored_when_disabled(self) -> None:
        """Token usage events are tracked internally but not emitted when disabled."""
        events = [
            event_notification(
                "thread/tokenUsage/updated",
                {
                    "usage": {
                        "inputTokens": 1000,
                        "outputTokens": 500,
                        "totalTokens": 1500,
                    }
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(emit_token_usage_events=False)
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

        usage_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_event_type") == "token_usage"
        ]
        assert usage_events == []

        # But internal tracking still works
        thread_id = adapter._room_threads.get("room-1")
        assert thread_id is not None
        usage = adapter._token_usage.get(thread_id)
        assert usage is not None
        assert usage.input_tokens == 1000

    @pytest.mark.asyncio
    async def test_usage_command_shows_token_usage(self) -> None:
        """/usage command shows accumulated token usage."""
        events = [
            event_notification(
                "thread/tokenUsage/updated",
                {
                    "usage": {
                        "inputTokens": 5000,
                        "outputTokens": 1000,
                        "reasoningTokens": 2000,
                        "totalTokens": 8000,
                    }
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

        # Now run /usage
        await deliver_message(
            adapter,
            make_platform_message(content="/usage"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        usage_msgs = [
            m for m in tools.messages_sent if "token usage" in m["content"].lower()
        ]
        assert len(usage_msgs) >= 1
        assert "8,000" in usage_msgs[0]["content"]
