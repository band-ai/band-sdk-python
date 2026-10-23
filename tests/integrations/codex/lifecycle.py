"""Stateful peers for Windows ownership rules that run on every OS."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Mapping
from typing import cast

from band.integrations.codex.stdio_client import CodexStdioClient
from band.integrations.codex.winspawn import Job, JobNotDrained


class StdinPeer:
    def __init__(self) -> None:
        self.closed = False

    def is_closing(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.closed = True


class HelperPeer:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.stdin = StdinPeer()
        self.stdout = None
        self.stderr = None
        self.pid = 1
        self.exits_on_kill = True

    def kill(self) -> None:
        if self.exits_on_kill:
            self.returncode = 1


class JobPeer:
    name = "test-job"

    def __init__(self, helper: HelperPeer) -> None:
        self.helper = helper
        self.active = 0
        self.drains = True
        self.closed = False
        self.terminations = 0

    def terminate(self) -> None:
        assert self.helper.returncode is not None, "helper may still add members"
        self.terminations += 1

    async def wait_until_idle(self, timeout: float) -> None:
        assert self.helper.stdin.closed, "termination must unblock stdin first"
        if not self.drains:
            raise JobNotDrained(self.active)
        self.active = 0

    def close(self) -> None:
        assert self.helper.returncode is not None, "helper is still alive"
        assert self.active == 0, "job members are still alive"
        self.closed = True


class WindowsLifecycle:
    def __init__(self) -> None:
        self.helper = HelperPeer()
        self.job = JobPeer(self.helper)
        self.client = CodexStdioClient(command=[sys.executable])
        self.creation_started = asyncio.Event()
        self.creation_allowed = asyncio.Event()
        self.creation_allowed.set()
        self.status_started = asyncio.Event()
        self.status: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self.read_fd: int | None = None

    def create_job(self) -> Job:
        return cast(Job, self.job)

    async def launch(
        self,
        *command: str,
        stdin: int,
        stdout: int,
        stderr: int,
        cwd: str | None,
        env: Mapping[str, str] | None,
        startupinfo: object,
        limit: int,
    ) -> asyncio.subprocess.Process:
        self.creation_started.set()
        await self.creation_allowed.wait()
        return cast(asyncio.subprocess.Process, self.helper)

    async def read_status(self, read_fd: int, timeout: float) -> str:
        self.read_fd = read_fd
        self.status_started.set()
        return await self.status
