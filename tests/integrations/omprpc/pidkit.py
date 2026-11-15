"""Model kernel PID reuse at the external process signal boundary."""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Sequence

import psutil
import pytest

from band.integrations.codex.winspawn import Job
from band.integrations.omprpc.scope import OmpProcessScope
from tests.integrations.omprpc.gate import GatedProcess


def refuse_process_signal(*args: object) -> None:
    raise PermissionError("Process ownership is temporarily not signalable")


def refuse_containment(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name == "nt":
        monkeypatch.setattr(Job, "terminate", refuse_process_signal)
        return
    monkeypatch.setattr(os, "killpg", refuse_process_signal)


class DeferredSessionSignal:
    """Keep real members alive while post-signal ownership lookup disappears."""

    def __init__(
        self,
        pgid: int,
        members: Sequence[GatedProcess],
        *,
        hide_after_signal: bool,
    ) -> None:
        self.pgid = pgid
        self.members = {member.process.pid for member in members}
        self.hide_after_signal = hide_after_signal
        self.signalled = False
        self.original_group = os.getpgid
        self.original_kill = os.kill
        self.original_environment = psutil.Process.environ

    def send(self, pgid: int, sig: int) -> None:
        assert pgid == self.pgid
        assert sig == signal.SIGKILL
        self.signalled = True

    def send_pid(self, pid: int, sig: int) -> None:
        if sig == signal.SIGKILL:
            assert pid in self.members
            self.signalled = True
            return
        self.original_kill(pid, sig)

    def group(self, pid: int) -> int:
        if self.signalled and self.hide_after_signal and pid in self.members:
            raise ProcessLookupError(pid)
        return self.original_group(pid)

    def environment(self, process: psutil.Process) -> dict[str, str]:
        if self.signalled and self.hide_after_signal and process.pid in self.members:
            raise psutil.NoSuchProcess(process.pid)
        return self.original_environment(process)

    def install(self, patch: pytest.MonkeyPatch) -> None:
        patch.setattr(os, "killpg", self.send)
        patch.setattr(os, "kill", self.send_pid)
        patch.setattr(os, "getpgid", self.group)
        patch.setattr(
            psutil.Process, "environ", lambda process: self.environment(process)
        )


class RecycledGroup:
    """Reuse a reaped leader's group id during membership enumeration."""

    def __init__(
        self, root: asyncio.subprocess.Process, unrelated: GatedProcess
    ) -> None:
        self.root = root
        self.unrelated = unrelated
        self.reused = False

    def check_leader(self, pid: int, sig: int) -> None:
        assert (pid, sig) == (self.root.pid, 0)
        if not self.reused:
            raise ProcessLookupError

    def group(self, pid: int) -> int:
        assert pid == self.unrelated.process.pid
        self.reused = True
        return self.root.pid

    def install(self, patch: pytest.MonkeyPatch) -> None:
        patch.setattr(psutil, "pids", lambda: [self.unrelated.process.pid])
        patch.setattr(os, "kill", self.check_leader)
        patch.setattr(os, "getpgid", self.group)


class RecycledEnvironment:
    """Expose one stale matching environment while the process identity changes."""

    def __init__(self, unrelated: GatedProcess, owner_pid: int) -> None:
        self.pid = unrelated.process.pid
        self.owner_environment = psutil.Process(owner_pid).environ()
        self.original_environment = psutil.Process.environ
        self.original_create_time = psutil.Process.create_time
        self.recycled = False

    def environment(self, process: psutil.Process) -> dict[str, str]:
        if process.pid == self.pid and not self.recycled:
            self.recycled = True
            return self.owner_environment
        return self.original_environment(process)

    def create_time(self, process: psutil.Process) -> float:
        created = self.original_create_time(process)
        return created + 1 if self.recycled and process.pid == self.pid else created

    def install(self, patch: pytest.MonkeyPatch) -> None:
        patch.setattr(
            psutil.Process, "environ", lambda process: self.environment(process)
        )
        patch.setattr(
            psutil.Process, "create_time", lambda process: self.create_time(process)
        )


class RetainedProcess(psutil.Process):
    """Retain real death detection while routing signals to a replacement."""

    def __init__(self, pid: int) -> None:
        super().__init__(pid)
        self.replacement: psutil.Process | None = None

    def kill(self) -> None:
        # Kernel PID allocation cannot be controlled; model only its signal target.
        assert self.replacement is not None
        self.replacement.kill()


def signal_scope(processes: list[psutil.Process]) -> None:
    scope = OmpProcessScope()
    scope.processes.update(processes)
    scope.signal()
