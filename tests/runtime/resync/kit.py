"""Shared helpers for reconciliation and resync tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx

from band.runtime.execution import ExecutionContext
from band.runtime.messages import PlatformMessage
from tests.runtime.helpers import (
    LifecyclePlatform,
)


class ReconciliationPeer(LifecyclePlatform):
    """Expose polling observations without test-side delays."""

    def __init__(self) -> None:
        super().__init__()
        self._next_read = asyncio.Event()

    def answer(self, request: httpx.Request) -> httpx.Response:
        response = super().answer(request)
        if request.url.path.endswith("/next"):
            self._next_read.set()
        return response

    async def wait_for_reads(self, count: int) -> None:
        while len(self.next_reads) < count:
            self._next_read.clear()
            await self._next_read.wait()


ReconciledRoom = tuple[ExecutionContext, ReconciliationPeer, list[str]]


def make_platform_message(
    msg_id: str = "msg-1", room_id: str = "room-1"
) -> PlatformMessage:
    return PlatformMessage(
        id=msg_id,
        room_id=room_id,
        content="Hello",
        sender_id="user-999",
        sender_type="User",
        sender_name="Tester",
        message_type="text",
        metadata={},
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
