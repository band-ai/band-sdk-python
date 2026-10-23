"""Client lifecycle and process gate fixtures for OMP RPC tests."""

from __future__ import annotations

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
    process_gate_session,
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
    async with process_gate_session() as gate:
        yield gate
