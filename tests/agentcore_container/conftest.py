"""A local identity peer and same-identity container instances bound to it."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest_asyncio

from band.integrations.uvicorn_server import ManagedUvicornServer
from tests.agentcore_container.kit import HOST, INSTANCES, Instance, launch, stop
from tests.agentcore_container.peer import IdentityPeer
from tests.ports import HeldPort


@pytest_asyncio.fixture(loop_scope="function")
async def identity_peer() -> AsyncIterator[tuple[IdentityPeer, str]]:
    """The peer and the platform URL it serves on."""
    peer = IdentityPeer(callers=INSTANCES)
    server = ManagedUvicornServer(peer.app, host=HOST, port=0)
    await server.start()
    try:
        yield peer, f"http://{HOST}:{server.bound_port}"
    finally:
        await server.stop()


@pytest_asyncio.fixture(loop_scope="function")
async def instances(
    identity_peer: tuple[IdentityPeer, str],
) -> AsyncIterator[list[Instance]]:
    """Launch every instance at once; each port is held until its instance starts."""
    _, platform_url = identity_peer
    held = [HeldPort(HOST) for _ in range(INSTANCES)]
    launched: list[Instance] = []
    try:
        for port in held:
            port.release()
            launched.append(await launch(port.port, platform_url))
        yield launched
    finally:
        for port in held:
            port.release()
        await asyncio.gather(*(stop(instance) for instance in launched))
