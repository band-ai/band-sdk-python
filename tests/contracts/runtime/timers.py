"""Advance synchronous ingress deadlines without changing socket readiness time."""

from __future__ import annotations

import threading
from collections.abc import Callable
from queue import Empty, Queue
from typing import Any


class ManualTimer:
    def __init__(
        self, clock: TimerClock, seconds: float, callback: Callable[[], None]
    ) -> None:
        self.clock = clock
        self.seconds = seconds
        self.callback = callback
        self.daemon = False
        self.deadline = 0.0
        self.cancelled = False
        self.fired = False

    def start(self) -> None:
        with self.clock.changed:
            self.deadline = self.clock.now + self.seconds
            self.clock.timers.append(self)
            self.clock.changed.notify_all()

    def cancel(self) -> None:
        with self.clock.changed:
            self.cancelled = True
            self.clock.changed.notify_all()


class TimerClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.changed = threading.Condition()
        self.timers: list[ManualTimer] = []

    def monotonic(self) -> float:
        return self.now

    def timer(self, seconds: float, callback: Callable[[], None]) -> ManualTimer:
        return ManualTimer(self, seconds, callback)

    def await_timer(self, count: int = 1) -> ManualTimer:
        with self.changed:
            if not self.changed.wait_for(lambda: len(self.timers) >= count, timeout=5):
                raise AssertionError("The request did not arm its ingress deadline")
            return self.timers[count - 1]

    def advance(self, seconds: float) -> None:
        with self.changed:
            self.now += seconds
            due = [
                timer
                for timer in self.timers
                if not timer.cancelled
                and not timer.fired
                and timer.deadline <= self.now
            ]
            for timer in due:
                timer.fired = True
        for timer in due:
            timer.callback()


def await_capacity(slots: threading.Semaphore) -> None:
    if not slots.acquire(timeout=5):
        raise AssertionError(
            "The completed ingress did not release its connection slot"
        )
    slots.release()


class DeadlineQueue(Queue[Any]):
    def __init__(self, clock: TimerClock, maxsize: int = 0) -> None:
        super().__init__(maxsize)
        self.clock = clock
        self.expired = False

    def expire(self) -> None:
        with self.not_empty:
            self.expired = True
            self.not_empty.notify_all()

    def get(self, block: bool = True, timeout: float | None = None) -> Any:
        if not block or timeout is None:
            return super().get(block=block, timeout=timeout)
        timer = self.clock.timer(timeout, self.expire)
        timer.start()
        try:
            with self.not_empty:
                while not self._qsize():
                    if self.expired:
                        raise Empty
                    if not self.not_empty.wait(timeout=5):
                        raise AssertionError(
                            "The callback deadline was not advanced or completed"
                        )
                item = self._get()
                self.not_full.notify()
                return item
        finally:
            timer.cancel()
