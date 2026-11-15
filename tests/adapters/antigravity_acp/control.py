"""Control an external peer's initialization, prompts, and permission requests."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any

from tests.adapters.antigravity_acp.peerconfig import PeerCommand


class PeerControl:
    def __init__(self, port: int) -> None:
        self.port = port
        self._connections: asyncio.Queue[
            tuple[asyncio.StreamReader, asyncio.StreamWriter]
        ] = asyncio.Queue()
        self._writers: list[asyncio.StreamWriter] = []

    def connected(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._writers.append(writer)
        self._connections.put_nowait((reader, writer))

    async def request(self) -> dict[str, Any]:
        return await self._exchange(PeerCommand.PERMISSION)

    async def prompted(self) -> None:
        await self._exchange(PeerCommand.PROMPTED)

    async def initializing(self) -> None:
        await self._exchange(PeerCommand.INITIALIZING)

    async def release_initialize(self) -> None:
        reader, writer = await self._connections.get()
        try:
            writer.write((PeerCommand.RELEASE_INITIALIZE + "\n").encode())
            await writer.drain()
        finally:
            self._connections.put_nowait((reader, writer))

    async def disconnected(self) -> bytes:
        reader, _ = await self._connections.get()
        return await reader.read()

    async def release_prompt(self) -> None:
        await self._exchange(PeerCommand.RELEASE_PROMPT)

    async def _exchange(self, command: PeerCommand) -> dict[str, Any]:
        reader, writer = await self._connections.get()
        try:
            writer.write((command + "\n").encode())
            await writer.drain()
            return json.loads(await reader.readline())
        finally:
            self._connections.put_nowait((reader, writer))

    async def close(self) -> None:
        for writer in self._writers:
            writer.close()
        for writer in self._writers:
            with suppress(ConnectionError):
                await writer.wait_closed()
