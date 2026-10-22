"""Best-effort custody of an OMP RPC process tree."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Mapping
from uuid import uuid4

import psutil

from band.integrations.codex.winspawn import session_pid_reused
from band.integrations.omprpc.errors import OmpProcessError

_PROCESS_SCOPE_VALUE = "1"


class OmpProcessScope:
    """Retain observable descendants for best-effort cleanup.

    The private PI_ marker survives native helper filters. Explicit environment
    replacement and restricted process inspection can hide detached children.
    Windows job membership is the primary containment; this marker catches
    descendants the job does not observe.
    """

    def __init__(self) -> None:
        self.key = f"PI_BAND_PROCESS_{uuid4().hex}"
        self.processes: set[psutil.Process] = set()

    def environment(self, base: Mapping[str, str] | None) -> dict[str, str]:
        return {
            **(os.environ if base is None else base),
            self.key: _PROCESS_SCOPE_VALUE,
        }

    def capture(self, root: asyncio.subprocess.Process) -> None:
        group_visible = not session_pid_reused(root)
        group_members: list[psutil.Process] = []
        getpgid = getattr(os, "getpgid", None)
        for pid in psutil.pids():
            # asyncio's child watcher remains the sole waiter for the root.
            if pid == 0 or pid == root.pid:
                continue
            try:
                member = psutil.Process(pid)
                created = member.create_time()
                marked = False
                try:
                    marked = member.environ().get(self.key) == _PROCESS_SCOPE_VALUE
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                in_group = False
                if group_visible and getpgid is not None:
                    try:
                        in_group = getpgid(pid) == root.pid
                    except (ProcessLookupError, PermissionError):
                        pass
                if not marked and not in_group:
                    continue
                current = psutil.Process(pid)
                if current.create_time() != created or current != member:
                    continue
                if marked:
                    self.processes.add(member)
                elif in_group:
                    group_members.append(member)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if group_visible and not session_pid_reused(root):
            self.processes.update(group_members)

    @staticmethod
    def _is_alive(process: psutil.Process) -> bool:
        try:
            return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False

    def signal(self) -> None:
        for process in self.processes:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass

    def close(self, root: asyncio.subprocess.Process, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        self.capture(root)
        alive = [process for process in self.processes if self._is_alive(process)]
        while alive:
            self.signal()
            psutil.wait_procs(alive, timeout=max(0.0, deadline - time.monotonic()))
            self.capture(root)
            alive = [process for process in self.processes if self._is_alive(process)]
            if alive and time.monotonic() >= deadline:
                raise OmpProcessError(
                    "OMP owned session members survived cleanup: "
                    f"{sorted(process.pid for process in alive)}"
                )
