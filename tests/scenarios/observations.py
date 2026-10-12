"""A change pulse the scenario tools share, so tests wait on observations
instead of sleeping."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

# Long enough for a real reconnect's backoff; short enough to fail a hung flow.
OBSERVATION_TIMEOUT_S = 10.0


class Observations:
    """One ordered journal every tool records into, pulsed on each entry."""

    def __init__(self) -> None:
        self.journal: list[str] = []
        self._changed = asyncio.Event()

    def record(self, entry: str) -> None:
        self.journal.append(entry)
        self.pulse()

    def story(self, room_id: str) -> list[str]:
        """Everything journaled about ``room_id``, in order, without the prefix."""
        prefix = f"{room_id}: "
        return [
            entry.removeprefix(prefix)
            for entry in self.journal
            if entry.startswith(prefix)
        ]

    def pulse(self) -> None:
        self._changed.set()
        self._changed = asyncio.Event()

    async def until(
        self, holds: Callable[[], bool], *, timeout: float = OBSERVATION_TIMEOUT_S
    ) -> None:
        """Wait for ``holds`` to become true; the timeout is the breaker."""
        async with asyncio.timeout(timeout):
            changed = self._changed
            while not holds():
                await changed.wait()
                changed = self._changed
