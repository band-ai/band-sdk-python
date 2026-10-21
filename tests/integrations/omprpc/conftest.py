"""Client lifecycle and process gate fixtures for OMP RPC tests."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

pytest.importorskip("psutil", reason="psutil not installed (band-sdk[omp])")

from band.integrations.omprpc import OmpRpcClient
from tests.integrations.omprpc.kit import (
    OMP_PEER_SCRIPT,
    ClientFactory,
    ProcessGate,
    kill_gate_processes,
)


@pytest_asyncio.fixture(loop_scope="function")
async def client_factory() -> AsyncIterator[ClientFactory]:
    clients: list[OmpRpcClient] = []

    async def build(scenario: str) -> OmpRpcClient:
        client = OmpRpcClient(
            (
                sys.executable,
                "-u",
                OMP_PEER_SCRIPT,
                "--scenario",
                scenario,
            )
        )
        clients.append(client)
        await client.start()
        return client

    try:
        yield build
    finally:
        for client in clients:
            await client.close()


@pytest_asyncio.fixture(loop_scope="function")
async def process_gate() -> AsyncIterator[ProcessGate]:
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
