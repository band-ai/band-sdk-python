"""Observable heartbeat transport boundary and watchdog lifecycle."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from phoenix_channels_python_client.client import PHXChannelsClient

from band.client.streaming.watchdog import HeartbeatWatchdog
from tests.websocket.conftest import fast_session_policy

DEAD_THRESHOLD_S = 8.0
CLOSE_REASON = "Heartbeat dead-threshold exceeded"


class WatchdogPeer:
    def __init__(self, *, connected: bool = True, fail_first: bool = False) -> None:
        self.connection: object | None = object() if connected else None
        self.close_calls: list[str] = []
        self.attempts: asyncio.Queue[str] = asyncio.Queue()
        self.fail_first = fail_first

    async def close_connection(self, reason: str) -> None:
        self.close_calls.append(reason)
        self.attempts.put_nowait(reason)
        if self.fail_first and len(self.close_calls) == 1:
            raise RuntimeError("close boom")


@asynccontextmanager
async def watching(peer: WatchdogPeer) -> AsyncIterator[HeartbeatWatchdog]:
    watchdog = HeartbeatWatchdog(
        fast_session_policy(heartbeat_interval_s=2.0, dead_threshold_s=DEAD_THRESHOLD_S)
    )
    task = None
    try:
        watchdog.start(cast(PHXChannelsClient, peer))
        task = watchdog._task
        assert task is not None
        yield watchdog
    finally:
        await watchdog.stop()
        assert task is None or task.done()
        assert watchdog._task is None
