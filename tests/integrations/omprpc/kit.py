"""Shared observers and process gates for OMP RPC tests."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any, cast

import psutil
import pytest
from pydantic import JsonValue

from band.integrations.codex.winspawn import Job
from band.integrations.omprpc import OmpProcessError, OmpRpcClient
from band.integrations.ompwire import (
    OmpCommandName,
    OmpContent,
    OmpEvent,
    OmpEventType,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
)
from tests.integrations import omppeer
from tests.integrations.omppeer import COMPLETE, FOLLOW_UP_PROMPT, ProcessReceipt
from tests.integrations.omprpc.cancellation import HeldCleanup

ClientFactory = Callable[[str], Awaitable[OmpRpcClient]]

# The fake OMP peer every RPC test launches as its subprocess.
OMP_PEER_SCRIPT = omppeer.__file__


class FailingOwnedClient(OmpRpcClient):
    fail_spawn = True

    async def spawn_owned_process(
        self, *command: str, **kwargs: Any
    ) -> asyncio.subprocess.Process:
        process = await super().spawn_owned_process(*command, **kwargs)
        if self.fail_spawn:
            raise ValueError("Owned spawn wrapper failed")
        return process


class SpawnBarrier:
    """Create the first subprocess, then hold returning it until released."""

    def __init__(
        self, spawn: Callable[..., Awaitable[asyncio.subprocess.Process]]
    ) -> None:
        self.spawn = spawn
        self.created = asyncio.Event()
        self.release = asyncio.Event()
        self.process: asyncio.subprocess.Process | None = None

    async def create(
        self, *command: str, **kwargs: object
    ) -> asyncio.subprocess.Process:
        process = await self.spawn(*command, **kwargs)
        if self.process is None:
            self.process = process
            self.created.set()
            await self.release.wait()
        return process


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
        if frame["type"] == OmpCommandName.PROMPT
    ]


def process_is_alive(process: psutil.Process) -> bool:
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def is_descendant(child: psutil.Process, ancestor: psutil.Process) -> bool:
    """True when ``ancestor`` is a living parent of ``child``.

    Windows Python launchers insert an extra process, so the gate child's
    ``parent_pid`` is not always the owned helper.
    """
    try:
        ancestor_pid = ancestor.pid
        ancestor_created = ancestor.create_time()
        current = child.parent()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    seen: set[int] = set()
    while current is not None:
        try:
            pid = current.pid
            if pid == ancestor_pid and current.create_time() == ancestor_created:
                return True
            if pid in seen:
                return False
            seen.add(pid)
            current = current.parent()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False
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


def refuse_containment(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name == "nt":
        monkeypatch.setattr(Job, "terminate", refuse_process_signal)
        return
    monkeypatch.setattr(os, "killpg", refuse_process_signal)


def session_ids(pids: Sequence[int]) -> list[int]:
    getsid = getattr(os, "getsid", None)
    assert getsid is not None
    return [getsid(pid) for pid in pids]


def job_member_pids(client: OmpRpcClient) -> set[int]:
    owner = client._windows_process
    assert owner is not None and owner.job is not None
    return set(owner.job._process_ids())


async def wait_for_descendants(gate: ProcessGate, count: int = 2) -> list[GatedProcess]:
    return [await gate.connection() for _ in range(count)]


async def descendants_started_with(
    gate: ProcessGate, root: asyncio.subprocess.Process, count: int = 2
) -> list[GatedProcess]:
    ancestor = await asyncio.to_thread(psutil.Process, root.pid)
    children: list[GatedProcess] = []
    while len(children) < count:
        child = await gate.connection()
        if is_descendant(child.process, ancestor):
            children.append(child)
    return children


async def crash_rpc_root(client: OmpRpcClient) -> None:
    root = owned_rpc_process(client)
    crashing = asyncio.create_task(client.request("crash"))
    async with asyncio.timeout(5):
        await root.wait()
    assert root.returncode == omppeer.CRASH_EXIT
    with pytest.raises(OmpProcessError):
        await crashing


@dataclass
class DescendantTree:
    client: OmpRpcClient
    cleanup: HeldCleanup
    starting: asyncio.Task[None]
    root: asyncio.subprocess.Process
    descendants: list[GatedProcess]


@asynccontextmanager
async def hold_after_stdin_closed(
    client: OmpRpcClient,
) -> AsyncIterator[asyncio.Task[None]]:
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def hold() -> None:
        loop.call_soon_threadsafe(entered.set)
        if not release.wait(timeout=10):
            raise TimeoutError("Test did not release after_stdin_closed")

    client.after_stdin_closed = hold
    closing = asyncio.create_task(client.close())
    try:
        async with asyncio.timeout(10):
            await entered.wait()
        yield closing
        if not closing.done():
            release.set()
            await closing
    finally:
        release.set()
        await asyncio.gather(closing, return_exceptions=True)


async def start_eof_client(
    gate: ProcessGate, *, stdio: str
) -> tuple[OmpRpcClient, GatedProcess, asyncio.subprocess.Process]:
    client = descendant_client(
        gate, scenario="eof-descendants", stdio=stdio, detached=True
    )
    await client.start()
    helper = await gate.connection()
    return client, helper, owned_rpc_process(client)


async def connect_descendant_tree(
    gate: ProcessGate, *, scenario: str, stdio: str
) -> DescendantTree:
    client = descendant_client(gate, scenario=scenario, stdio=stdio)
    cleanup = HeldCleanup(client)
    starting = asyncio.create_task(client.start())
    descendants = await wait_for_descendants(gate)
    return DescendantTree(
        client=client,
        cleanup=cleanup,
        starting=starting,
        root=owned_rpc_process(client),
        descendants=descendants,
    )


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
