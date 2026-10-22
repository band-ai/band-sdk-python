"""Expire a real command only after its external peer confirms readiness."""

from __future__ import annotations

import selectors
from collections.abc import Callable, Generator
from contextlib import contextmanager
from math import isclose
from types import SimpleNamespace
from unittest.mock import patch


class CommandDeadline:
    def __init__(self, ready: Callable[[], None]) -> None:
        self.ready = ready
        self.now = 0.0
        self.expected_timeout = 10.0
        self.observed_timeout: float | None = None
        self.expired = False

    def monotonic(self) -> float:
        return self.now

    def selector(self) -> DeadlineSelector:
        return DeadlineSelector(self)


class DeadlineSelector(selectors.DefaultSelector):
    def __init__(self, clock: CommandDeadline) -> None:
        super().__init__()
        self.clock = clock

    def select(
        self, timeout: float | None = None
    ) -> list[tuple[selectors.SelectorKey, int]]:
        if timeout is None or timeout <= 0:
            raise AssertionError("The command did not retain a positive deadline")
        self.clock.ready()
        remaining = self.clock.expected_timeout - self.clock.now
        if not isclose(timeout, remaining, rel_tol=0.0, abs_tol=1e-9):
            raise AssertionError(
                f"Expected the configured 10-second deadline; received {timeout!r} seconds"
            )
        self.clock.observed_timeout = timeout
        self.clock.now = self.clock.expected_timeout
        self.clock.expired = True
        return []


@contextmanager
def command_deadline(
    ready: Callable[[], None],
) -> Generator[CommandDeadline, None, None]:
    clock = CommandDeadline(ready)
    with (
        patch("remote_agent.commands.time", SimpleNamespace(monotonic=clock.monotonic)),
        patch(
            "remote_agent.commands.selectors",
            SimpleNamespace(
                DefaultSelector=clock.selector, EVENT_READ=selectors.EVENT_READ
            ),
        ),
    ):
        yield clock
