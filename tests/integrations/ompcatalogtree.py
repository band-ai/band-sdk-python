"""Observable descendants whose CLI launcher can exit before probe cleanup."""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import threading
import time
from typing import Self

import psutil

from tests.integrations.omppeer import ProcessReceipt


def receipt() -> ProcessReceipt:
    return ProcessReceipt(
        pid=os.getpid(), parent_pid=os.getppid(), created=psutil.Process().create_time()
    )


def start_tree_child(
    role: str, port: int
) -> tuple[subprocess.Popen[bytes], ProcessReceipt]:
    child = subprocess.Popen(
        (
            sys.executable,
            "-u",
            "-m",
            "tests.integrations.ompcatalogtree",
            role,
            str(port),
        ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert child.stdout is not None
    with child.stdout:
        observed = ProcessReceipt.model_validate_json(child.stdout.readline())
    return child, observed


def hold_tree_child(role: str, port: int) -> None:
    observed = receipt()
    if role == "leaf":
        sys.stdout.write(observed.model_dump_json() + "\n")
        sys.stdout.flush()
        threading.Event().wait()
        return
    with socket.create_connection(("127.0.0.1", port), timeout=10) as connection:
        connection.settimeout(None)
        connection.sendall((observed.model_dump_json() + "\n").encode())
        sys.stdout.write(observed.model_dump_json() + "\n")
        sys.stdout.flush()
        while command := connection.recv(1):
            if command == b"s":
                _child, created = start_tree_child("leaf", port)
                connection.sendall((created.model_dump_json() + "\n").encode())
            else:
                return


class CatalogTreeGate:
    def __init__(self) -> None:
        self.port = 0
        self._server: asyncio.Server | None = None
        self._connections: asyncio.Queue[
            tuple[asyncio.StreamReader, asyncio.StreamWriter]
        ] = asyncio.Queue()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self.receipts: list[ProcessReceipt] = []

    async def __aenter__(self) -> Self:
        self._server = await asyncio.start_server(self._accept, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    def _accept(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._connections.put_nowait((reader, writer))

    async def connected(self) -> None:
        async with asyncio.timeout(10):
            self._reader, self._writer = await self._connections.get()
            self.receipts.append(
                ProcessReceipt.model_validate_json(await self._reader.readline())
            )

    async def spawn_late_child(self) -> None:
        assert self._reader is not None and self._writer is not None
        self._writer.write(b"s")
        await self._writer.drain()
        self.receipts.append(
            ProcessReceipt.model_validate_json(await self._reader.readline())
        )

    @staticmethod
    def _alive(observed: ProcessReceipt) -> psutil.Process | None:
        try:
            process = psutil.Process(observed.pid)
            if process.create_time() != observed.created:
                return None
            if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                return process
        except psutil.NoSuchProcess:
            return None
        return None

    def assert_settled(self) -> None:
        assert self.receipts
        assert not [
            observed.pid for observed in self.receipts if self._alive(observed)
        ], (
            "A catalog probe completed while its launcher's descendants were still running"
        )

    def _cleanup(self) -> None:
        for observed in self.receipts:
            if (process := self._alive(observed)) is not None:
                try:
                    process.kill()
                except psutil.NoSuchProcess:
                    pass
        deadline = time.monotonic() + 5
        while any(self._alive(observed) for observed in self.receipts):
            if time.monotonic() >= deadline:
                raise RuntimeError("Catalog fixture descendants survived teardown")
            time.sleep(0.01)

    async def __aexit__(self, *_errors: object) -> None:
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except OSError:
                pass
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        await asyncio.to_thread(self._cleanup)


if __name__ == "__main__":
    hold_tree_child(sys.argv[1], int(sys.argv[2]))
