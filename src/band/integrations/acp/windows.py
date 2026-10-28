"""Own a Windows ACP process tree independently of its connection lifetime."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from acp import connect_to_agent
from acp.interfaces import Client
from acp.transports import default_environment

from band.integrations.acp.transport import ACPConnectionProtocol
from band.integrations.codex.winspawn import (
    WindowsProcess,
    await_returncode,
    close_stdin,
)

ACP_PROCESS_SHUTDOWN_TIMEOUT_S = 2.0


class WindowsACPProcess:
    """Retain native job ownership through startup failure and repeated cleanup."""

    def __init__(self) -> None:
        self._owner = WindowsProcess()

    @property
    def process(self) -> asyncio.subprocess.Process | None:
        return self._owner.process

    @asynccontextmanager
    async def connect(
        self,
        client: Client,
        command: list[str],
        *,
        env: dict[str, str] | None,
        cwd: str | None,
        limit: int,
        use_unstable_protocol: bool,
    ) -> AsyncIterator[tuple[ACPConnectionProtocol, asyncio.subprocess.Process]]:
        process = await self._owner.start_command(
            command,
            cwd=cwd,
            env={**default_environment(), **(env or {})},
            limit=limit,
        )
        if process.stdin is None or process.stdout is None:
            raise RuntimeError("Windows ACP process requires stdin and stdout pipes")
        connection = connect_to_agent(
            client,
            process.stdin,
            process.stdout,
            use_unstable_protocol=use_unstable_protocol,
        )
        try:
            yield cast(ACPConnectionProtocol, connection), process
        finally:
            await connection.close()

    async def close(self) -> None:
        """Close every owned descendant before a connection waits for pipe EOF."""
        if (process := self.process) is not None:
            await close_stdin(process)
            await await_returncode(process, ACP_PROCESS_SHUTDOWN_TIMEOUT_S)
        await self._owner.close(ACP_PROCESS_SHUTDOWN_TIMEOUT_S)
