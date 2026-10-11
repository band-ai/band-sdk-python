"""Per-room reconciliation timing on the event loop's monotonic clock."""

from __future__ import annotations

import random


class ResyncSchedule:
    """Keep periodic recovery independent of queue activity."""

    def __init__(self, base: float, maximum: float) -> None:
        self.base = base
        self.maximum = max(base, maximum)
        self.interval = base
        self.due_at: float | None = None

    def start(self, now: float) -> None:
        self.interval = self.base
        self.due_at = now + random.uniform(0, self.base)

    def reset(self, now: float) -> None:
        self.interval = self.base
        if self.due_at is not None:
            self.due_at = min(self.due_at, now + self.base)

    def complete(self, now: float, *, empty: bool) -> None:
        self.interval = min(self.interval * 2, self.maximum) if empty else self.base
        if self.due_at is not None and self.due_at <= now:
            self.due_at = now + self.interval
