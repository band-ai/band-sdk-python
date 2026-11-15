"""Synchronization and tuning for in-memory monitoring peers."""

from __future__ import annotations

import asyncio

from band.integrations.desktop_app.event_relay import RoomEventBroker
from band.integrations.desktop_app.settings import RoomViewTuning

STALE_GRACE_S = RoomViewTuning().band_room_stale_grace_s


class WaitingBroker(RoomEventBroker):
    """Signal entry to the real broker wait before the event loop yields."""

    def __init__(self) -> None:
        super().__init__()
        self.waiting = asyncio.Event()

    async def wait(
        self, chat_id: str, *, after_version: int, timeout_seconds: int
    ) -> bool:
        self.waiting.set()
        return await super().wait(
            chat_id, after_version=after_version, timeout_seconds=timeout_seconds
        )
