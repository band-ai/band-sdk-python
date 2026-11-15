"""The originals a room keeps, in receive order, until their turn settles."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterator

from band.platform.event import PlatformEvent
from band.runtime.originals import delivery_id


class RetainedEvents:
    """An ordered event line that answers "is this delivery already held" in O(1)."""

    def __init__(self) -> None:
        self.events: deque[PlatformEvent] = deque()
        self.delivery_ids: set[str] = set()

    def __iter__(self) -> Iterator[PlatformEvent]:
        return iter(self.events)

    def add(self, event: PlatformEvent) -> None:
        """Append the event unless a delivery with the same id is already held."""
        identity = delivery_id(event)
        if identity is not None:
            if identity in self.delivery_ids:
                return
            self.delivery_ids.add(identity)
        self.events.append(event)

    def remove(self, doomed: Callable[[PlatformEvent], bool]) -> list[PlatformEvent]:
        """Drop every event the predicate selects; return them in line order."""
        kept: deque[PlatformEvent] = deque()
        removed: list[PlatformEvent] = []
        for event in self.events:
            (removed if doomed(event) else kept).append(event)
        self.events = kept
        self.delivery_ids.difference_update(
            identity for event in removed if (identity := delivery_id(event))
        )
        return removed
