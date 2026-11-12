"""Per-subscription readiness signalled from confirmed Phoenix joins."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

INITIAL_GENERATION = 1


@dataclass(frozen=True)
class RoomReadiness:
    """A confirmed join of one chat room subscription, counted from 1."""

    room_id: str
    generation: int

    @property
    def after_gap(self) -> bool:
        """Delivery resumed after a gap: catch up from history."""
        return self.generation > INITIAL_GENERATION


RoomReadyCallback = Callable[[RoomReadiness], None]


class JoinHook:
    """Phoenix ``on_join_response``: counts confirmed joins, before their messages."""

    def __init__(self, *, topic: str, on_join: Callable[[int], None]) -> None:
        self._topic = topic
        self._on_join = on_join
        self._generation = 0

    def __call__(self, response: object) -> None:
        self._generation += 1
        try:
            self._on_join(self._generation)
        except Exception:
            # Phoenix drops the topic when its join hook raises.
            logger.exception("[WebSocket] Join callback failed for %s", self._topic)
