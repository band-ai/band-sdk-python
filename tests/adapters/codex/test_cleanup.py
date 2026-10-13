"""Codex adapter: cleanup."""

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


class TestCleanup:
    @pytest.mark.asyncio
    async def test_on_cleanup_removes_per_room_token_usage(self) -> None:
        """on_cleanup for a room also removes the thread's token usage."""
        events = [
            event_notification(
                "thread/tokenUsage/updated",
                {
                    "usage": {
                        "inputTokens": 500,
                        "outputTokens": 100,
                        "totalTokens": 600,
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

        # Verify usage was tracked
        thread_id = adapter._room_threads.get("room-1")
        assert thread_id is not None
        assert thread_id in adapter._token_usage

        # Add a second room so cleanup doesn't close the client entirely
        adapter._room_threads["room-2"] = "other-thread"

        await adapter.on_cleanup("room-1")
        assert thread_id not in adapter._token_usage
