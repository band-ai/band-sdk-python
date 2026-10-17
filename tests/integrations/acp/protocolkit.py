"""Real ACP client/server exchanges for session lifecycle and failure outcomes."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any

from acp import connect_to_agent
from acp.agent.connection import AgentSideConnection

from band.integrations.acp.server import ACPServer
from band.integrations.acp.server_adapter import BandACPServerAdapter


@dataclass(frozen=True)
class ReceivedUpdate:
    session_id: str
    chunk: object
    notification_fields: dict[str, Any]


class RecordingClient:
    def __init__(self) -> None:
        self.updates: list[ReceivedUpdate] = []
        self.received = asyncio.Event()

    async def session_update(
        self, *, session_id: str, update: object, **kwargs: Any
    ) -> None:
        self.updates.append(ReceivedUpdate(session_id, update, kwargs))
        self.received.set()

    async def wait_for_updates(self, count: int) -> None:
        while len(self.updates) < count:
            self.received.clear()
            await asyncio.wait_for(self.received.wait(), 1)


@asynccontextmanager
async def protocol_pair(
    adapter: BandACPServerAdapter,
) -> AsyncIterator[tuple[Any, RecordingClient]]:
    client_sock, agent_sock = socket.socketpair()
    reader_c, writer_c = await asyncio.open_connection(sock=client_sock)
    reader_a, writer_a = await asyncio.open_connection(sock=agent_sock)
    client = RecordingClient()
    server_connection = AgentSideConnection(
        ACPServer(adapter), writer_a, reader_a, use_unstable_protocol=True
    )
    connection = connect_to_agent(client, writer_c, reader_c)
    try:
        yield connection, client
    finally:
        for closable in (connection, server_connection):
            with suppress(Exception):
                await closable.close()
        for writer in (writer_c, writer_a):
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()
