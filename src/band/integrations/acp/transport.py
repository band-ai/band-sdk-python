"""ACP connection contracts and stdio/TCP transport configuration."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol

from acp import connect_to_agent
from acp.interfaces import Client
from acp.schema import (
    LoadSessionResponse,
    NewSessionResponse,
    SetSessionConfigOptionResponse,
)

logger = logging.getLogger(__name__)
ACP_STDIO_LIMIT_BYTES = 16 * 1024 * 1024


def tcp_spawn_process(
    host: str,
    port: int,
    *,
    limit: int = ACP_STDIO_LIMIT_BYTES,
) -> Callable[..., AbstractAsyncContextManager[tuple[object, object]]]:
    """Connect to ACP over TCP using the stdio-shaped spawn interface."""

    @asynccontextmanager
    async def _connect(
        client: Client,
        *_command: object,
        env: dict[str, str] | None = None,
        transport_kwargs: dict[str, object] | None = None,
    ) -> AsyncIterator[tuple[object, object]]:
        del _command, env, transport_kwargs  # subprocess-only; unused for TCP
        reader, writer = await asyncio.open_connection(host, port, limit=limit)
        # connect_to_agent argument order is (client, input_stream=writer,
        # output_stream=reader) and it type-guards writer: StreamWriter /
        # reader: StreamReader. Unlike spawn_agent_process it does no cleanup,
        # so we close the connection and transport ourselves.
        conn = connect_to_agent(client, writer, reader)
        try:
            yield conn, writer
        finally:
            try:
                await conn.close()
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    logger.debug("Error awaiting TCP writer close", exc_info=True)

    return _connect


class ACPConnectionProtocol(Protocol):
    """Protocol for the ACP agent connection returned by spawn_agent_process."""

    async def initialize(self, *, protocol_version: int) -> object: ...

    async def authenticate(self, *, method_id: str) -> object: ...

    async def new_session(
        self, *, cwd: str, mcp_servers: list[object]
    ) -> NewSessionResponse: ...

    async def load_session(
        self,
        *,
        cwd: str,
        session_id: str,
        mcp_servers: list[object],
    ) -> LoadSessionResponse | None: ...

    async def prompt(self, *, session_id: str, prompt: list[object]) -> object: ...

    async def set_config_option(
        self,
        *,
        config_id: str,
        session_id: str,
        value: str,
    ) -> SetSessionConfigOptionResponse | None: ...

    async def close_session(self, session_id: str) -> object: ...

    async def cancel(self, session_id: str) -> None: ...


class ACPSpawnContextProtocol(Protocol):
    """Protocol for the spawn_agent_process async context manager."""

    async def __aenter__(self) -> tuple[ACPConnectionProtocol, object]: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> object: ...
