"""Explicit stream, kernel-exit and control barriers for owned process tests."""

from __future__ import annotations

import asyncio
import ctypes
import json
import os
import select
import signal
from typing import NamedTuple

from tests.contracts.lifecycle.kit import Child, Spawn


def pidfd(pid: int) -> int:
    """Use libc when the Python build predates its pidfd wrapper."""
    opener = getattr(os, "pidfd_open", None)
    if opener is not None:
        return opener(pid)
    library = ctypes.CDLL(None, use_errno=True)
    native = library.pidfd_open
    native.argtypes = (ctypes.c_int, ctypes.c_uint)
    native.restype = ctypes.c_int
    descriptor = native(pid, 0)
    if descriptor < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return descriptor


class ExitedLeader(Child):
    def __init__(self) -> None:
        super().__init__()
        self.returncode = 0
        self.exit.set_result(0)

    def close_pipes(self) -> None:
        if not self.stdout.at_eof():
            self.stdout.feed_eof()
        if not self.stderr.at_eof():
            self.stderr.feed_eof()

    def kill(self) -> None:
        self.close_pipes()


class ExitedSpawn(Spawn):
    def __init__(self) -> None:
        super().__init__()
        self.exited_child = ExitedLeader()
        self.child = self.exited_child

    def kill_group(self, pid: int, signum: int) -> None:
        assert pid == self.child.pid
        assert signum == signal.SIGKILL
        self.exited_child.close_pipes()


class ExitProtocol(asyncio.subprocess.SubprocessStreamProtocol):
    def __init__(
        self,
        *,
        limit: int,
        loop: asyncio.AbstractEventLoop,
        exited: asyncio.Event,
    ) -> None:
        super().__init__(limit=limit, loop=loop)
        self.exited = exited

    def process_exited(self) -> None:
        super().process_exited()
        self.exited.set()


class Connection(NamedTuple):
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    pid: int


class DescendantPeer:
    def __init__(self) -> None:
        loop = asyncio.get_running_loop()
        self.connections: dict[str, asyncio.Future[Connection]] = {
            role: loop.create_future() for role in ("leader", "descendant")
        }
        self.leader_exited = asyncio.Event()
        self.server: asyncio.Server | None = None
        self.writers: list[asyncio.StreamWriter] = []

    async def start(self) -> int:
        self.server = await asyncio.start_server(self.connected, "127.0.0.1", 0)
        return int(self.server.sockets[0].getsockname()[1])

    async def connected(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.writers.append(writer)
        payload = json.loads(await reader.readline())
        connection = Connection(reader, writer, payload["pid"])
        self.connections[payload["role"]].set_result(connection)

    def protocol(self, *, limit: int, loop: asyncio.AbstractEventLoop) -> ExitProtocol:
        return ExitProtocol(limit=limit, loop=loop, exited=self.leader_exited)

    async def ready(self, role: str) -> Connection:
        return await asyncio.wait_for(asyncio.shield(self.connections[role]), 10)

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
        for writer in self.writers:
            writer.close()
        await asyncio.gather(
            *(writer.wait_closed() for writer in self.writers), return_exceptions=True
        )
        if self.server is not None:
            await self.server.wait_closed()


class DescendantFence:
    """Pin the actual descendant until its kernel exit, avoiding PID reuse."""

    def __init__(self, pid: int, group: int) -> None:
        self.pid = pid
        self.group = group
        self.loop = asyncio.get_running_loop()
        self.fd = pidfd(pid)
        self.exited: asyncio.Future[None] = self.loop.create_future()
        self.loop.add_reader(self.fd, self.notice_exit)

    def notice_exit(self) -> None:
        self.loop.remove_reader(self.fd)
        if not self.exited.done():
            self.exited.set_result(None)

    async def close(self) -> None:
        try:
            if select.select([self.fd], [], [], 0)[0]:
                self.notice_exit()
            if not self.exited.done():
                try:
                    if os.getpgid(self.pid) == self.group:
                        os.killpg(self.group, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            await asyncio.wait_for(asyncio.shield(self.exited), 10)
        finally:
            self.loop.remove_reader(self.fd)
            os.close(self.fd)
