"""Time an OpenCode turn spends parked on a human."""

from __future__ import annotations

import asyncio


class HumanWait:
    """The idle event the turn watcher blocks on, plus the human-wait clock.

    Idle means no manual ask is parked on a human. ``park`` clears it only when
    an ask is actually forwarded to the room; ``release`` sets it again the
    moment the last one resolves.
    """

    def __init__(self) -> None:
        self._idle = asyncio.Event()
        self._idle.set()
        self._total = 0.0
        self._started: float | None = None

    @property
    def is_idle(self) -> bool:
        return self._idle.is_set()

    async def wait_until_idle(self) -> None:
        await self._idle.wait()

    @property
    def seconds(self) -> float:
        """Seconds this turn has spent parked on a human, including right now.

        The turn watcher adds this to ``turn_timeout_s``. Deliberation is
        bounded by the ask's own expiry timer, so charging it to the compute
        budget would abort healthy work that resumed after a slow approval.
        """
        parked = 0.0 if self._started is None else _clock() - self._started
        return self._total + parked

    def park(self) -> None:
        """Hand an ask to the room and start the clock."""
        if self._started is None:
            self._started = _clock()
        self._idle.clear()

    def release(self) -> None:
        """No ask is parked: bank the waited time and unblock the watcher."""
        if self._started is not None:
            self._total += _clock() - self._started
            self._started = None
        self._idle.set()


def _clock() -> float:
    """Loop time, so the clock is immune to wall-clock changes."""
    return asyncio.get_running_loop().time()
