"""Codex adapter: leaving a room forgets its per-room state."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from band.adapters.codex import (
    CodexAdapterConfig,
    PendingApproval,
)
from band.runtime.decisions import DecisionRegistry
from tests.adapters.codex.kit import (
    ROOM_ID,
    send_bootstrap,
    wire_codex_room,
)
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

    @pytest.mark.asyncio
    async def test_cleanup_before_start(self) -> None:
        """Calling on_cleanup on a freshly constructed adapter should not raise."""
        adapter = make_codex_adapter(
            FakeCodexClient(),
            config=CodexAdapterConfig(),
        )
        # No on_started called — cleanup should be safe (idempotent)
        await adapter.on_cleanup("room-x")

    @pytest.mark.asyncio
    async def test_cleanup_clears_pending_approvals(self) -> None:
        """on_cleanup should evict all pending approvals for the given room."""
        fake_client = FakeCodexClient()
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        await adapter.on_started("Bot", "desc")

        # Manually inject a pending approval for room-1
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        registry: DecisionRegistry[PendingApproval] = DecisionRegistry()
        registry.register_keyed(
            PendingApproval(
                request_id=1,
                method="item/tool/call",
                summary="test",
                created_at=datetime.now(UTC),
                future=fut,
            ),
            key="tok-1",
        )
        adapter._pending_approvals[ROOM_ID] = registry
        wire_codex_room(adapter, fake_client, ROOM_ID)
        adapter._room_threads[ROOM_ID] = "thr-1"

        await adapter.on_cleanup(ROOM_ID)

        assert adapter._open_approval_count(ROOM_ID) == 0
        # The future should have been resolved (declined)
        assert fut.done()
