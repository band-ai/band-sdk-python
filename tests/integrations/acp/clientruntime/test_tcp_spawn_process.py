"""The TCP connect-only spawn_process seam."""

from __future__ import annotations

import asyncio

import pytest
from acp.client.connection import ClientSideConnection

from band.integrations.acp.client_runtime import (
    ACPCollectingClient,
    tcp_spawn_process,
)


class TestTCPSpawnProcess:
    """Tests for the TCP connect-only spawn_process seam.

    Uses a real loopback server (no patching): the factory must open a socket,
    build a live ACP connection over it, yield ``(conn, writer)``, and close both
    on exit — ignoring the subprocess-shaped args the runtime forwards.
    """

    @pytest.mark.asyncio
    async def test_connects_and_cleans_up(self) -> None:
        async def _handle(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                await reader.read()  # hold the connection until the client closes
            finally:
                writer.close()

        server = await asyncio.start_server(_handle, "127.0.0.1", 0)
        host, port = server.sockets[0].getsockname()[:2]

        async with server:
            spawn = tcp_spawn_process(host, port)
            client = ACPCollectingClient()

            # Forward subprocess-shaped args the runtime would pass; TCP ignores them.
            cm = spawn(
                client,
                "ignored-executable",
                "ignored-arg",
                env=None,
                transport_kwargs={"limit": 1024},
            )
            conn, writer = await cm.__aenter__()
            try:
                assert isinstance(writer, asyncio.StreamWriter)
                # A real ClientSideConnection built from the socket, not a mock.
                assert isinstance(conn, ClientSideConnection)
            finally:
                await cm.__aexit__(None, None, None)

            assert writer.is_closing()

    @pytest.mark.asyncio
    async def test_cleans_up_when_body_raises(self) -> None:
        """The connect CM must close the transport even if the caller raises
        mid-session (e.g. initialize fails) — the `finally` path, not just happy exit."""

        async def _handle(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                await reader.read()
            finally:
                writer.close()

        server = await asyncio.start_server(_handle, "127.0.0.1", 0)
        host, port = server.sockets[0].getsockname()[:2]

        async with server:
            spawn = tcp_spawn_process(host, port)
            captured: dict[str, asyncio.StreamWriter] = {}

            with pytest.raises(RuntimeError, match="boom"):
                async with spawn(ACPCollectingClient()) as (_conn, writer):
                    captured["writer"] = writer
                    raise RuntimeError("boom")

            assert captured["writer"].is_closing()
