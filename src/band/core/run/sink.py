"""Runtime event sink that assigns envelopes and records emits."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from band.core.contracts import EnvelopedTurnEvent, TurnEvent


@dataclass
class RecordingEventSink:
    """In-memory sink that assigns envelopes and records every emit."""

    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    _sequence: int = field(default=0, init=False, repr=False)
    _events: list[EnvelopedTurnEvent] = field(default_factory=list, init=False)
    _observers: list[Callable[[EnvelopedTurnEvent], None]] = field(
        default_factory=list, init=False, repr=False
    )

    @property
    def events(self) -> Sequence[EnvelopedTurnEvent]:
        return tuple(self._events)

    def add_observer(
        self, observer: Callable[[EnvelopedTurnEvent], None]
    ) -> Callable[[], None]:
        self._observers.append(observer)

        def remove() -> None:
            try:
                self._observers.remove(observer)
            except ValueError:
                pass

        return remove

    async def emit(self, event: TurnEvent) -> None:
        self._sequence += 1
        enveloped = EnvelopedTurnEvent(
            run_id=self.run_id,
            sequence=self._sequence,
            timestamp=time.time(),
            event=event,
        )
        self._events.append(enveloped)
        for observer in self._observers:
            observer(enveloped)
