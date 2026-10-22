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
import pytest
from pydantic import JsonValue

from band.integrations.omprpc import OmpRpcClient
from band.integrations.ompwire import (
    OmpContent,
    OmpEvent,
    OmpEventType,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
)
from tests.adapters.waiting import spin
from tests.integrations import omppeer
from tests.integrations.omppeer import COMPLETE, FOLLOW_UP_PROMPT, ProcessReceipt
from tests.toolkit.timecontrol import ControlledClock, controlled_clock

COMPLETION_BUDGET_S = 8.0
CALLBACK_SETTLEMENT_BUDGET_S = 2 * COMPLETION_BUDGET_S
BACKGROUND_WINDOW_S = 4 * COMPLETION_BUDGET_S
NO_CALLBACK_GRACE_S = 0.0

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

    @property
    def outline(self) -> list[OmpEventType]:
        return [event.type for event in self.events]

    @property
    def update_ids(self) -> list[str | None]:
        return [
            event.messageId
            for event in self.events
            if event.type == OmpEventType.MESSAGE_UPDATE
        ]

    async def on_event(self, event: OmpEvent) -> None:
        self.events.append(event)
        self.queue.put_nowait(event)

    async def on_tool(self, call: OmpHostToolCall) -> OmpToolResult:
        return OmpToolResult(content=[OmpContent(type="text", text=call.toolName)])

    async def on_ui(self, request: OmpUIRequest) -> OmpUIResponse:
        return OmpUIResponse(cancelled=True)

    async def wait_for(self, event_type: OmpEventType) -> OmpEvent:
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


def owned_rpc_process(client: OmpRpcClient) -> asyncio.subprocess.Process:
    process = client._proc
    assert process is not None
    return process


async def second_prompt(client: OmpRpcClient) -> Observer:
    observer = Observer()
    async with asyncio.timeout(5):
        await prompt_task(client, observer, FOLLOW_UP_PROMPT)
    assert COMPLETE in observer.assistant_texts
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
    gate: ProcessGate,
    *,
    scenario: str = "descendants",
    stdio: str = "discard",
    detached: bool = False,
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
            *(("--detached-children",) if detached else ()),
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


@asynccontextmanager
async def unrelated_gate_child(gate: ProcessGate) -> AsyncIterator[GatedProcess]:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-u",
        OMP_PEER_SCRIPT,
        "--scenario",
        "gated-child",
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


class BlockedEventObserver(Observer):
    """Hold the peer's update callback after its result and settlement."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.resume = asyncio.Event()

    async def on_event(self, event: OmpEvent) -> None:
        if event.type == OmpEventType.MESSAGE_UPDATE:
            self.entered.set()
            await self.resume.wait()
        await super().on_event(event)


class StubbornCallbackObserver(Observer):
    """Hold claimed callback work until the test explicitly releases it."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.cancel_seen = asyncio.Event()
        self.resume = asyncio.Event()
        self.finished = asyncio.Event()
        self.retired = asyncio.Event()

    async def hold_callback(self) -> None:
        owner = asyncio.current_task()
        assert owner is not None
        owner.add_done_callback(lambda _task: self.retired.set())
        self.entered.set()
        try:
            while not self.resume.is_set():
                try:
                    await self.resume.wait()
                except asyncio.CancelledError:
                    self.cancel_seen.set()
        finally:
            self.finished.set()

    async def release(self) -> None:
        self.resume.set()
        if self.entered.is_set():
            await self.finished.wait()


class StubbornToolObserver(StubbornCallbackObserver):
    """Keep claimed host work alive until the test explicitly releases it."""

    async def on_tool(self, call: OmpHostToolCall) -> OmpToolResult:
        await self.hold_callback()
        return OmpToolResult(content=[])


class StubbornEventObserver(StubbornCallbackObserver):
    """Record callback admission while keeping its event worker alive."""

    async def on_event(self, event: OmpEvent) -> None:
        await super().on_event(event)
        await self.hold_callback()


async def snapshots_observed(client: OmpRpcClient, count: int = 1) -> None:
    """Dispatch completed snapshots before advancing a completion deadline."""
    while cast(int, (await client.request("checkpoint"))["snapshots"]) < count:
        pass
    await client.request("checkpoint")
    await spin()


async def state_request_count(client: OmpRpcClient) -> int:
    return sum(
        frame["type"] == "get_state" for frame in await checkpoint_frames(client)
    )


async def state_requests_observed(client: OmpRpcClient, count: int = 1) -> None:
    """Observe a sent state request even when its response is deliberately lost."""
    while await state_request_count(client) < count:
        pass
    await client.request("checkpoint")
    await spin()


async def peer_process(client: OmpRpcClient) -> psutil.Process:
    checkpoint = await client.request("checkpoint")
    pid = checkpoint["pid"]
    assert isinstance(pid, int)
    process = await asyncio.to_thread(psutil.Process, pid)
    await asyncio.to_thread(process.create_time)
    return process


@dataclass(frozen=True)
class CompletionSession:
    client: OmpRpcClient
    clock: ControlledClock
    peer: psutil.Process


@asynccontextmanager
async def completion_session(
    client_factory: ClientFactory, scenario: str
) -> AsyncIterator[CompletionSession]:
    async with controlled_clock() as clock:
        client = await clock.run_io(client_factory(scenario))
        try:
            peer = await peer_process(client)
            client.completion_timeout_s = COMPLETION_BUDGET_S
            yield CompletionSession(client, clock, peer)
        finally:
            await clock.run_io(client.close())


async def background_claim_observed(observer: Observer) -> OmpEvent:
    while True:
        event = await observer.wait_for(OmpEventType.AGENT_END)
        if event.awaitingAsyncWork is True:
            return event
