"""A real ACP process and pipe holder with explicit lifecycle signals."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from typing import Any

import pytest
from acp import spawn_agent_process

from band.integrations.acp import client_adapter
from band.integrations.acp.client_adapter import ACPClientAdapter
from tests.integrations.acp.acp_toolkit.harness import DEFAULT_ROOM, send_to
from tests.integrations.acp.acp_toolkit.peer import ExitStage, stdio_peer_config
from tests.integrations.acp.acp_toolkit.pipepeer import (
    CONTROL_HOST,
    PipeCommand,
    PipeRole,
    control_line,
)
from tests.paths import REPO_ROOT


class PipeProcess:
    """Own the control server until the agent and its inherited pipes close."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.agent_connected = asyncio.Event()
        self.holder_ready = asyncio.Event()
        self.closing = asyncio.Event()
        self.transport_closing = asyncio.Event()
        self.exited = asyncio.Event()
        self.stdout_closed = asyncio.Event()
        self.stdout_read = asyncio.Event()
        self.stderr_closed = asyncio.Event()
        self._spawn_allowed = asyncio.Event()
        self._spawn_allowed.set()
        self._exit_requested = asyncio.Event()
        self._holder_released = asyncio.Event()
        self._holder_creation_settled = asyncio.Event()
        self._transport: asyncio.SubprocessTransport | None = None
        self.adapter: ACPClientAdapter[Any]
        self._connections: list[asyncio.Task[None]] = []
        self._operations: list[asyncio.Task[None]] = []
        self._monkeypatch = monkeypatch

    @property
    def returncode(self) -> int | None:
        return self._transport.get_returncode() if self._transport is not None else None

    def hold_startup(self) -> None:
        self._spawn_allowed.clear()

    def release_holder(self) -> None:
        self._holder_released.set()

    def connect(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._connections.append(asyncio.create_task(self._serve(reader, writer)))

    async def _serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            role = PipeRole((await reader.readline()).decode().strip())
            match role:
                case PipeRole.AGENT:
                    self.agent_connected.set()
                    await self._spawn_allowed.wait()
                    await self._send(writer, PipeCommand.START)
                    await self._exit_requested.wait()
                    await self._send(writer, PipeCommand.EXIT)
                case PipeRole.HOLDER:
                    self.holder_ready.set()
                    self._holder_creation_settled.set()
                    await self._holder_released.wait()
                    await self._send(writer, PipeCommand.RELEASE)
            await reader.read()
        except ConnectionError:
            # Process exit may reset its control connection.
            pass
        finally:
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()

    @staticmethod
    async def _send(writer: asyncio.StreamWriter, command: PipeCommand) -> None:
        writer.write(control_line(command))
        await writer.drain()

    def observe(
        self,
        factory: Callable[[], asyncio.SubprocessProtocol],
    ) -> asyncio.SubprocessProtocol:
        protocol = factory()
        on_connect = protocol.connection_made
        on_exit = protocol.process_exited
        on_pipe = protocol.pipe_connection_lost

        def connected(transport: asyncio.SubprocessTransport) -> None:
            self._transport = transport
            on_connect(transport)

        def exited() -> None:
            on_exit()
            self.exited.set()
            self._holder_creation_settled.set()

        def pipe_closed(fd: int, error: Exception | None) -> None:
            on_pipe(fd, error)
            match fd:
                case 1:
                    self.stdout_closed.set()
                case 2:
                    self.stderr_closed.set()

        self._monkeypatch.setattr(protocol, "connection_made", connected)
        self._monkeypatch.setattr(protocol, "process_exited", exited)
        self._monkeypatch.setattr(protocol, "pipe_connection_lost", pipe_closed)
        return protocol

    @asynccontextmanager
    async def spawn(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        async with spawn_agent_process(*args, **kwargs) as process:
            _, child = process
            assert child.stdout is not None
            read = child.stdout.read

            async def read_stdout(n: int = -1) -> bytes:
                if self.closing.is_set():
                    self.stdout_read.set()
                return await read(n)

            self._monkeypatch.setattr(child.stdout, "read", read_stdout)
            try:
                yield process
            finally:
                self.closing.set()
                # Transport shutdown must not kill a delayed holder creator.
                await self._holder_creation_settled.wait()
                self.transport_closing.set()

    def begin_start(self) -> asyncio.Task[None]:
        return self._start(send_to(self.adapter, DEFAULT_ROOM))

    async def start(self) -> None:
        await self.begin_start()
        await self.holder_ready.wait()

    def begin_cleanup(self) -> asyncio.Task[None]:
        return self._start(self.adapter.on_cleanup(DEFAULT_ROOM))

    def _start(self, operation: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        task = asyncio.create_task(operation)
        self._operations.append(task)
        return task

    async def exit_agent(self) -> None:
        self._exit_requested.set()
        await self.exited.wait()

    async def wait_for_stdout_cleanup(self, cleanup: asyncio.Task[None]) -> None:
        reading = asyncio.create_task(self.stdout_read.wait())
        try:
            await asyncio.wait({reading, cleanup}, return_when=asyncio.FIRST_COMPLETED)
            assert not cleanup.done(), "Cleanup finished before confirming stdout EOF"
            await reading
        finally:
            reading.cancel()
            await asyncio.gather(reading, return_exceptions=True)

    def release(self) -> None:
        """Release current and future connections before waiting for cleanup."""
        self._spawn_allowed.set()
        self.release_holder()
        self._exit_requested.set()

    async def finish(self) -> None:
        self.release()
        try:
            await self.adapter.cleanup_all()
        finally:
            await asyncio.gather(*self._operations, return_exceptions=True)
            if self._transport is not None:
                await self.exited.wait()
                await self.stdout_closed.wait()
                await self.stderr_closed.wait()


PipeProcessFactory = Callable[[], AbstractAsyncContextManager[PipeProcess]]


@asynccontextmanager
async def controlled_pipes(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[PipeProcess]:
    with monkeypatch.context() as scoped:
        peer = PipeProcess(scoped)
        server = await asyncio.start_server(peer.connect, CONTROL_HOST, 0)
        port = server.sockets[0].getsockname()[1]
        loop = asyncio.get_running_loop()
        subprocess_exec = loop.subprocess_exec

        async def observed_spawn(
            factory: Callable[[], asyncio.SubprocessProtocol], *args: Any, **kwargs: Any
        ) -> tuple[asyncio.SubprocessTransport, asyncio.SubprocessProtocol]:
            return await subprocess_exec(lambda: peer.observe(factory), *args, **kwargs)

        scoped.setattr(loop, "subprocess_exec", observed_spawn)
        scoped.setattr(client_adapter, "spawn_agent_process", peer.spawn)
        peer.adapter = ACPClientAdapter(
            stdio_peer_config(ExitStage.EOF, [], control_port=port),
            workspace_for_room=lambda room_id: str(REPO_ROOT),
        )
        try:
            await peer.adapter.on_started("Controlled peer", "Real subprocess")
            yield peer
        finally:
            try:
                await peer.finish()
            finally:
                server.close()
                await server.wait_closed()
                await asyncio.gather(*peer._connections)
