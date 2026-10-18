"""Transport controls for host probes, independent of room ownership."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from acp import spawn_agent_process

from tests.integrations.acp.acp_toolkit.harness import make_acp_connection


class ProbeSpawn:
    """Script the ACP transport while keeping its runtime and handshake real."""

    def __init__(self) -> None:
        self.connection = make_acp_connection()
        self.commands: list[tuple[str, ...]] = []
        self.environments: list[dict[str, str] | None] = []
        self.directories: list[str | None] = []
        self.unstable_protocols: list[bool] = []
        self.open_count = 0
        self.start_error: Exception | None = None
        self.initializing = asyncio.Event()
        self.release_initialize = asyncio.Event()

    async def hold_initialize(self, **kwargs: Any) -> None:
        self.initializing.set()
        await self.release_initialize.wait()

    @asynccontextmanager
    async def __call__(
        self,
        client: object,
        *command: str,
        env: dict[str, str] | None,
        cwd: str | None,
        use_unstable_protocol: bool = False,
        **kwargs: Any,
    ) -> AsyncIterator[tuple[Any, None]]:
        if self.start_error is not None:
            raise self.start_error
        self.commands.append(tuple(command))
        self.environments.append(env)
        self.directories.append(cwd)
        self.unstable_protocols.append(use_unstable_protocol)
        self.open_count += 1
        try:
            yield self.connection, None
        finally:
            self.open_count -= 1


class ProbeProcess:
    """A real ACP process whose creation and first close can be held or refused."""

    def __init__(self, context: AbstractAsyncContextManager[Any]) -> None:
        self.context = context
        self.process: asyncio.subprocess.Process | None = None
        self.opened = asyncio.Event()
        self.release_creation: asyncio.Event | None = None
        self.refuse_close = False

    async def __aenter__(self) -> Any:
        connection, self.process = await self.context.__aenter__()
        self.opened.set()
        if self.release_creation is not None:
            try:
                await self.release_creation.wait()
            except asyncio.CancelledError:
                # Resource creation can finish after the caller's deadline.
                await self.release_creation.wait()
        return connection, self.process

    async def __aexit__(self, *exc_info: object) -> None:
        if self.refuse_close:
            raise RuntimeError("ACP process exit refused")
        await self.context.__aexit__(None, None, None)

    async def close(self) -> None:
        """Release a refused context after its runtime has retried physical exit."""
        self.refuse_close = False
        if self.release_creation is not None:
            self.release_creation.set()
        await self.context.__aexit__(None, None, None)


class ProbeProcesses:
    """Spawn real ACP children with reproducible partial-creation and exit failures."""

    def __init__(
        self, *, refuse_close: bool = False, hold_creation: bool = False
    ) -> None:
        self.children: list[ProbeProcess] = []
        self.refuse_close = refuse_close
        self.hold_creation = hold_creation
        self.spawned = asyncio.Event()

    def __call__(self, *args: Any, **kwargs: Any) -> ProbeProcess:
        child = ProbeProcess(spawn_agent_process(*args, **kwargs))
        child.refuse_close = self.refuse_close
        if self.hold_creation:
            child.release_creation = asyncio.Event()
        self.children.append(child)
        self.spawned.set()
        return child

    async def close(self) -> None:
        for child in self.children:
            await child.close()
