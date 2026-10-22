"""Synchronize real qualification children before injecting a wait deadline."""

from __future__ import annotations

import subprocess
import threading
import time
from contextlib import ExitStack
from pathlib import Path
from types import TracebackType
from typing import Any, Self
from unittest.mock import patch

from contracts.deploy import qualificationprocess


def live(pid: int) -> bool:
    """Observe a still-running fixture process, excluding an unreaped zombie."""
    try:
        return Path("/proc", str(pid), "stat").read_text().rsplit(") ", 1)[1][0] != "Z"
    except FileNotFoundError:
        return False


def await_file(path: Path, process: subprocess.Popen[Any]) -> None:
    """Wait for explicit child readiness; the deadline only guards a hung fixture."""
    deadline = time.monotonic() + 10
    polling = threading.Event()
    while not path.exists():
        if process.poll() is not None:
            raise AssertionError("Fixture exited before its readiness barrier")
        if time.monotonic() >= deadline:
            raise AssertionError("Fixture did not reach its readiness barrier")
        polling.wait(0.01)


class ReadyWait:
    """Expire a controlled deadline after the actual process reaches its gate."""

    def __init__(
        self, process: subprocess.Popen[Any], ready: Path, force: bool
    ) -> None:
        self.process = process
        self.ready = ready
        self.force = force
        self.wait = process.wait
        self.calls = 0

    def expired(self, timeout: float | None = None) -> int:
        self.calls += 1
        if self.calls == 1 or (self.force and self.calls == 2):
            if timeout is None:
                raise AssertionError("The supervised wait did not supply a deadline")
            if self.calls == 1:
                await_file(self.ready, self.process)
            raise subprocess.TimeoutExpired(self.process.args, timeout)
        return self.wait(timeout=timeout)


class TimeoutAtReady:
    """Keep real spawn/signals/cleanup and control only the driver's wait expiry."""

    def __init__(self, ready: Path, *, force: bool = False) -> None:
        self.ready = ready
        self.force = force
        self.spawn = subprocess.Popen
        self.stack = ExitStack()

    def start(self, *args: Any, **kwargs: Any) -> subprocess.Popen[Any]:
        process = self.spawn(*args, **kwargs)
        waiting = ReadyWait(process, self.ready, self.force)
        self.stack.enter_context(
            patch.object(process, "wait", side_effect=waiting.expired)
        )
        return process

    def __enter__(self) -> Self:
        self.stack.enter_context(
            patch.object(
                qualificationprocess.subprocess, "Popen", side_effect=self.start
            )
        )
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stack.__exit__(kind, value, traceback)
