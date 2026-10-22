"""Expire a sandbox callback only after its real worker announces entry."""

from __future__ import annotations

import os
import selectors
from collections.abc import Generator
from contextlib import contextmanager
from math import isclose
from types import SimpleNamespace
from unittest.mock import patch

READY_MARKER = b"contract-test-callback-entered\n"
CALLBACK_LOOP = f"import os; os.write(1, {READY_MARKER!r})\n    while True: pass"


class CallbackDeadline:
    def __init__(self) -> None:
        self.now = 0.0
        self.expected_timeout = 10.0
        self.observed_timeout: float | None = None
        self.output = b""
        self.entered = False
        self.expired = False

    def monotonic(self) -> float:
        return self.now

    def read(self, descriptor: int, count: int) -> bytes:
        value = os.read(descriptor, count)
        self.output += value
        self.entered = READY_MARKER in self.output
        return value

    def selector(self) -> CallbackSelector:
        return CallbackSelector(self)


class CallbackSelector(selectors.DefaultSelector):
    def __init__(self, clock: CallbackDeadline) -> None:
        super().__init__()
        self.clock = clock

    def select(
        self, timeout: float | None = None
    ) -> list[tuple[selectors.SelectorKey, int]]:
        if self.clock.entered:
            if timeout is None or timeout <= 0:
                raise AssertionError("The callback did not retain a positive deadline")
            remaining = self.clock.expected_timeout - self.clock.now
            if not isclose(timeout, remaining, rel_tol=0.0, abs_tol=1e-9):
                raise AssertionError(
                    f"Expected the configured 10-second deadline; received {timeout!r} seconds"
                )
            self.clock.observed_timeout = timeout
            self.clock.now = self.clock.expected_timeout
            self.clock.expired = True
            return []
        ready = super().select(5)
        if not ready:
            raise AssertionError("The owned callback did not announce entry")
        return ready


@contextmanager
def callback_deadline() -> Generator[CallbackDeadline, None, None]:
    clock = CallbackDeadline()
    observed_os = SimpleNamespace(**{**vars(os), "read": clock.read})
    with (
        patch("remote_agent.commands.os", observed_os),
        patch("remote_agent.commands.time", SimpleNamespace(monotonic=clock.monotonic)),
        patch(
            "remote_agent.commands.selectors",
            SimpleNamespace(
                DefaultSelector=clock.selector, EVENT_READ=selectors.EVENT_READ
            ),
        ),
    ):
        yield clock
