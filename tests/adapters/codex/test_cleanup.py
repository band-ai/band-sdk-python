"""Codex adapter: leaving a room forgets its thread's token usage."""

from __future__ import annotations

import pytest

from tests.adapters.codex.kit import ROOM_ID, send_bootstrap
from tests.adapters.codexturns import (
    FakeCodexClient,
    event_notification,
    make_codex_adapter,
    turn_completed,
)


class TestCleanup:
    @pytest.mark.asyncio
    async def test_on_cleanup_removes_per_room_token_usage(self) -> None:
        # A leaked entry has no room-visible effect; it only grows the map.
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
        adapter = make_codex_adapter(FakeCodexClient(events=events))
        await adapter.on_started("Agent", "A coding agent")
        await send_bootstrap(adapter)
        thread_id = adapter._room_threads[ROOM_ID]
        assert thread_id in adapter._token_usage

        await adapter.on_cleanup(ROOM_ID)

        assert thread_id not in adapter._token_usage
