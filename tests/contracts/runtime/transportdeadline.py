"""Expire a transport write after its real child pipe becomes blocked."""

from __future__ import annotations

import selectors
from collections.abc import Generator
from contextlib import contextmanager
from math import isclose
from types import SimpleNamespace
from typing import TypedDict
from unittest.mock import patch


class EngineOptions(TypedDict, total=False):
    command: list[str]
    format: str


class WriteDeadline:
    def __init__(self) -> None:
        self.now = 0.0
        self.expected_timeout = 10.0
        self.observed_timeout: float | None = None
        self.blocked = False

    def monotonic(self) -> float:
        return self.now

    def selector(self) -> WriteSelector:
        return WriteSelector(self)


class WriteSelector(selectors.DefaultSelector):
    def __init__(self, clock: WriteDeadline) -> None:
        super().__init__()
        self.clock = clock

    def select(
        self, timeout: float | None = None
    ) -> list[tuple[selectors.SelectorKey, int]]:
        ready = super().select(0)
        if ready:
            return ready
        if timeout is None or timeout <= 0:
            raise AssertionError("The blocked write did not retain a positive deadline")
        if any(key.events != selectors.EVENT_WRITE for key in self.get_map().values()):
            raise AssertionError("The request did not fill the stopped engine's pipe")
        self.clock.blocked = True
        remaining = self.clock.expected_timeout - self.clock.now
        if not isclose(timeout, remaining, rel_tol=0.0, abs_tol=1e-9):
            raise AssertionError(
                f"Expected the configured 10-second deadline; received {timeout!r} seconds"
            )
        self.clock.observed_timeout = timeout
        self.clock.now = self.clock.expected_timeout
        return []


@contextmanager
def write_deadline() -> Generator[WriteDeadline, None, None]:
    clock = WriteDeadline()
    with (
        patch(
            "remote_agent.transport.time", SimpleNamespace(monotonic=clock.monotonic)
        ),
        patch(
            "remote_agent.transport.selectors",
            SimpleNamespace(
                DefaultSelector=clock.selector,
                EVENT_READ=selectors.EVENT_READ,
                EVENT_WRITE=selectors.EVENT_WRITE,
            ),
        ),
    ):
        yield clock
