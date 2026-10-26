"""Own real descendant processes connected to a local TCP gate."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass

import psutil

from band.integrations.omprpc.custody import kill_retained_process, process_is_alive
from tests.integrations.ompgate import GATE_COMMAND, GateRole, ProcessReceipt


def kill_gate_processes(processes: list[psutil.Process]) -> None:
    for process in processes:
        kill_retained_process(process)
    _, remaining = psutil.wait_procs(processes, timeout=5)
    if alive := [process.pid for process in remaining if process_is_alive(process)]:
        raise RuntimeError(f"Process gate cleanup left live processes: {alive}")


@dataclass(frozen=True)
class GatedProcess:
    receipt: ProcessReceipt
    process: psutil.Process


class ProcessGate:
    def __init__(self) -> None:
        self.port = 0
        self.connections: asyncio.Queue[
            tuple[asyncio.StreamReader, asyncio.StreamWriter]
        ] = asyncio.Queue()
        self.writers: list[asyncio.StreamWriter] = []
        self.processes: list[psutil.Process] = []

    def accept(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.writers.append(writer)
        self.connections.put_nowait((reader, writer))

    async def connection(self) -> GatedProcess:
        reader, _ = await self.connections.get()
        receipt = ProcessReceipt.model_validate_json(await reader.readline())
        process = await asyncio.to_thread(psutil.Process, receipt.pid)
        if await asyncio.to_thread(process.create_time) != receipt.created:
            raise ProcessLookupError(f"Gate process identity changed: {receipt.pid}")
        self.processes.append(process)
        return GatedProcess(receipt, process)


@asynccontextmanager
async def process_gate_session() -> AsyncIterator[ProcessGate]:
    gate = ProcessGate()
    server = await asyncio.start_server(gate.accept, "127.0.0.1", 0)
    try:
        sockets = server.sockets
        assert sockets
        gate.port = sockets[0].getsockname()[1]
        yield gate
    finally:
        server.close()
        for writer in gate.writers:
            writer.close()
        await asyncio.gather(
            *(writer.wait_closed() for writer in gate.writers), return_exceptions=True
        )
        await server.wait_closed()
        await asyncio.to_thread(kill_gate_processes, gate.processes)


@asynccontextmanager
async def unrelated_gate_child(gate: ProcessGate) -> AsyncIterator[GatedProcess]:
    process = await asyncio.create_subprocess_exec(
        *GATE_COMMAND,
        "--scenario",
        GateRole.CHILD,
        "--gate-port",
        str(gate.port),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=os.name != "nt",
    )
    try:
        yield await gate.connection()
    finally:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
        async with asyncio.timeout(5):
            await process.wait()
