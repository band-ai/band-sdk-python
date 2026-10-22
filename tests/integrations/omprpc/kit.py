"""Shared observers and process gates for OMP RPC tests."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import cast

import psutil
from pydantic import JsonValue

from band.integrations.omprpc import OmpRpcClient
from band.integrations.ompwire import (
    OmpContent,
    OmpEvent,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
)
from tests.integrations import omppeer
from tests.integrations.omppeer import ProcessReceipt

ClientFactory = Callable[[str], Awaitable[OmpRpcClient]]

# The fake OMP peer every RPC test launches as its subprocess.
OMP_PEER_SCRIPT = omppeer.__file__


class Observer:
    def __init__(self) -> None:
        self.events: list[OmpEvent] = []
        self.queue: asyncio.Queue[OmpEvent] = asyncio.Queue()

    @property
    def assistant_texts(self) -> list[str]:
        return [
            part.text
            for event in self.events
            if event.message is not None
            and event.message.role == "assistant"
            and isinstance(event.message.content, list)
            for part in event.message.content
            if part.text is not None
        ]

    async def on_event(self, event: OmpEvent) -> None:
        self.events.append(event)
        self.queue.put_nowait(event)

    async def on_tool(self, call: OmpHostToolCall) -> OmpToolResult:
        return OmpToolResult(content=[OmpContent(type="text", text=call.toolName)])

    async def on_ui(self, request: OmpUIRequest) -> OmpUIResponse:
        return OmpUIResponse(cancelled=True)

    async def wait_for(self, event_type: str) -> OmpEvent:
        async with asyncio.timeout(5):
            while True:
                event = await self.queue.get()
                if event.type == event_type:
                    return event


def prompt_task(
    client: OmpRpcClient, observer: Observer, message: str = "first"
) -> asyncio.Task[None]:
    return asyncio.create_task(
        client.prompt(
            message,
            on_event=observer.on_event,
            on_tool=observer.on_tool,
            on_ui=observer.on_ui,
        )
    )


async def second_prompt(client: OmpRpcClient) -> Observer:
    observer = Observer()
    async with asyncio.timeout(5):
        await prompt_task(client, observer, "second")
    assert any(
        event.message is not None
        and isinstance(event.message.content, list)
        and any(content.text == "complete" for content in event.message.content)
        for event in observer.events
    )
    return observer


async def announced_prompt(
    client: OmpRpcClient,
    observer: Observer,
    entered: asyncio.Event,
    message: str,
) -> None:
    entered.set()
    await client.prompt(
        message,
        on_event=observer.on_event,
        on_tool=observer.on_tool,
        on_ui=observer.on_ui,
    )


async def queue_prompt(
    client: OmpRpcClient, message: str = "queued"
) -> asyncio.Task[None]:
    entered = asyncio.Event()
    task = asyncio.create_task(announced_prompt(client, Observer(), entered, message))
    # The coroutine reaches the occupied admission lock before this waiter resumes.
    await entered.wait()
    return task


def descendant_client(
    gate: ProcessGate, *, scenario: str = "descendants", stdio: str = "discard"
) -> OmpRpcClient:
    return OmpRpcClient(
        (
            sys.executable,
            "-u",
            OMP_PEER_SCRIPT,
            "--scenario",
            scenario,
            "--gate-port",
            str(gate.port),
            "--child-stdio",
            stdio,
        )
    )


async def checkpoint_frames(client: OmpRpcClient) -> list[dict[str, JsonValue]]:
    checkpoint = await client.request("checkpoint")
    return cast(list[dict[str, JsonValue]], checkpoint["frames"])


async def sent_prompt_messages(client: OmpRpcClient) -> list[str]:
    return [
        cast(str, frame["message"])
        for frame in await checkpoint_frames(client)
        if frame["type"] == "prompt"
    ]


def process_is_alive(process: psutil.Process) -> bool:
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def kill_gate_processes(processes: list[psutil.Process]) -> None:
    for process in processes:
        with suppress(psutil.NoSuchProcess):
            process.kill()
    deadline = time.monotonic() + 5
    while alive := [process.pid for process in processes if process_is_alive(process)]:
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Process gate cleanup left live processes: {alive}")
        time.sleep(0.01)


@dataclass(frozen=True)
class GatedProcess:
    receipt: ProcessReceipt
    process: psutil.Process


async def descendant_liveness(descendants: Sequence[GatedProcess]) -> list[bool]:
    return [
        await asyncio.to_thread(process_is_alive, child.process)
        for child in descendants
    ]


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
        async with asyncio.timeout(5):
            reader, _ = await self.connections.get()
            receipt = ProcessReceipt.model_validate_json(await reader.readline())
        process = await asyncio.to_thread(psutil.Process, receipt.pid)
        self.processes.append(process)
        assert await asyncio.to_thread(process.create_time) == receipt.created
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


def refuse_process_signal(*args: object) -> None:
    raise PermissionError("Process ownership is temporarily not signalable")


class DeferredSessionSignal:
    """Keep real members alive while their post-signal group lookup disappears."""

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

    def send(self, pgid: int, sig: int) -> None:
        assert pgid == self.pgid
        assert sig == signal.SIGKILL
        self.signalled = True

    def group(self, pid: int) -> int:
        if self.signalled and self.hide_after_signal and pid in self.members:
            raise ProcessLookupError(pid)
        return self.original_group(pid)
