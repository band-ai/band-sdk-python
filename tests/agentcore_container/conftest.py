"""A local identity peer and same-identity container instances bound to it."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest_asyncio

from band.integrations.uvicorn_server import ManagedUvicornServer
from tests.agentcore_container.kit import HOST, INSTANCES, Instance, launch, stop
from tests.agentcore_container.peer import IdentityPeer


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
    """Launch every instance at once; each binds a port the OS assigns, so none is reserved."""
    _, platform_url = identity_peer
    launched: list[Instance] = []
    try:
        for _ in range(INSTANCES):
            launched.append(await launch(platform_url))
        yield launched
    finally:
        await asyncio.gather(*(stop(instance) for instance in launched))
