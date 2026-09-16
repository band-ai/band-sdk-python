from __future__ import annotations

import asyncio
import contextlib
import logging

from band_sdk_core import SessionPolicy
from phoenix_channels_python_client.client import PHXChannelsClient

logger = logging.getLogger(__name__)


class HeartbeatWatchdog:
    """Force-closes a connection once dead_threshold_s passes with no
    heartbeat ack, so a silently dead socket gets reconnected instead of
    sitting idle."""

    def __init__(self, policy: SessionPolicy) -> None:
        self.policy = policy
        self._deadline: float = 0.0
        self._task: asyncio.Task[None] | None = None

    def reset_deadline(self) -> None:
        self._deadline = (
            asyncio.get_running_loop().time() + self.policy.dead_threshold_s
        )

    def start(self, client: PHXChannelsClient) -> None:
        """Begin watching `client`. Takes it as an argument (rather than
        reading it from the caller later) so a stale watchdog left over
        from a superseded initial-connect attempt can never act on
        whatever instance the caller has since moved on to."""
        self.reset_deadline()
        self._task = asyncio.create_task(self._loop(client))

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    async def _loop(self, client: PHXChannelsClient) -> None:
        while True:
            await self._sleep_until_deadline()
            await self._force_close_if_stale(client)

    async def _sleep_until_deadline(self) -> None:
        """Sleep until ``self._deadline``, re-reading it after each wake so
        an ack (which pushes it forward via `reset_deadline`) reschedules
        the sleep instead of firing early."""
        while True:
            remaining = self._deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return
            await asyncio.sleep(remaining)

    async def _force_close_if_stale(self, client: PHXChannelsClient) -> None:
        """Force-close ``client`` if it still has a live connection.

        ``close_connection`` failures are caught and logged -- this is the
        only watchdog for the rest of `client`'s lifetime, so it must not
        die silently on one bad close.
        """
        self.reset_deadline()
        if client.connection is None:
            # Already disconnected (e.g. mid initial-connect or backoff);
            # nothing to force-close, and nothing to warn about.
            return
        logger.warning(
            "[WebSocket] No heartbeat ack within %.2fs; forcing reconnect",
            self.policy.dead_threshold_s,
        )
        try:
            await client.close_connection("Heartbeat dead-threshold exceeded")
        except Exception:
            logger.exception("[WebSocket] Failed to force-close dead connection")
