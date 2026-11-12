"""Per-room subscription readiness signalled from confirmed Phoenix joins."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

INITIAL_GENERATION = 1


@dataclass(frozen=True)
class RoomReadiness:
    """A confirmed join of one chat room subscription.

    ``generation`` counts this subscription's confirmed joins from 1. Every
    later generation follows a gap: messages sent while the room was not
    joined were not delivered, so catch up from history.
    """

    room_id: str
    generation: int

    @property
    def after_gap(self) -> bool:
        """Whether delivery resumed after an interruption."""
        return self.generation > INITIAL_GENERATION


RoomReadyCallback = Callable[[RoomReadiness], None]


class RoomReadinessHook:
    """Phoenix ``on_join_response`` hook that signals one subscription's joins.

    Phoenix calls it synchronously for every successful join reply, before the
    topic is marked joined, so it precedes every message callback of that
    join and the return of the initial join.
    """

    def __init__(self, *, room_id: str, on_ready: RoomReadyCallback) -> None:
        self._room_id = room_id
        self._on_ready = on_ready
        self._generation = 0

    def __call__(self, response: object) -> None:
        self._generation += 1
        readiness = RoomReadiness(room_id=self._room_id, generation=self._generation)
        try:
            self._on_ready(readiness)
        except Exception:
            # Phoenix treats a raising join hook as a configuration failure and
            # drops the topic; a consumer bug must not end the subscription.
            logger.exception(
                "[WebSocket] on_ready callback failed for room %s", self._room_id
            )
