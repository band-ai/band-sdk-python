"""Own control channels for an ACP agent and its inherited-pipe descendant."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, suppress

from band.integrations.acp.client_runtime import ACPRuntime
from tests.integrations.acp.acp_toolkit.peer import ExitStage, stdio_peer_config
from tests.integrations.acp.acp_toolkit.pipepeer import (
    PipeCommand,
    PipeRole,
    control_line,
)
from tests.paths import REPO_ROOT

OwnedPeerFactory = Callable[[ExitStage], AbstractAsyncContextManager["OwnedPipePeer"]]


class OwnedPipePeer:
    """Observe real process death through independently owned control sockets."""

    def __init__(self, stage: ExitStage, port: int) -> None:
        config = stdio_peer_config(stage, [], control_port=port)
        self.runtime = ACPRuntime(
            command=list(config.command), env=config.env, cwd=str(REPO_ROOT)
        )
        self.holder_ready = asyncio.Event()
        self.holder_closed = asyncio.Event()
        self.agent_closed = asyncio.Event()
        self.exit_requested = asyncio.Event()
        self.connections: list[asyncio.Task[None]] = []
        self._writers: dict[PipeRole, asyncio.StreamWriter] = {}

    def connect(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.connections.append(asyncio.create_task(self._serve(reader, writer)))

    async def _serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        role = PipeRole((await reader.readline()).decode().strip())
        self._writers[role] = writer
        try:
            match role:
                case PipeRole.AGENT:
                    writer.write(control_line(PipeCommand.START))
                    await writer.drain()
                    await self.exit_requested.wait()
                    writer.write(control_line(PipeCommand.EXIT))
                    await writer.drain()
                case PipeRole.HOLDER:
                    self.holder_ready.set()
            await reader.read()
        except ConnectionError:
            pass
        finally:
            match role:
                case PipeRole.AGENT:
                    self.agent_closed.set()
                case PipeRole.HOLDER:
                    self.holder_closed.set()
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()

    async def exit_agent(self) -> None:
        self.exit_requested.set()
        await self.agent_closed.wait()

    async def reject_initialize(self) -> None:
        writer = self._writers[PipeRole.AGENT]
        writer.write(control_line(PipeCommand.REJECT_INITIALIZE))
        await writer.drain()

    async def finish(self) -> None:
        self.exit_requested.set()
        holder = self._writers.get(PipeRole.HOLDER)
        if holder is not None and not holder.is_closing():
            with suppress(ConnectionError):
                holder.write(control_line(PipeCommand.RELEASE))
                await holder.drain()
        await self.runtime.stop()
        await asyncio.gather(*self.connections)
