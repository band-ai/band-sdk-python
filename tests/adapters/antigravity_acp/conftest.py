"""Start and close real stdio ACP peers in per-test directories."""

from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest_asyncio

from tests.ports import HeldPort

if TYPE_CHECKING:
    from tests.adapters.antigravity_acp.control import PermissionControl
    from tests.adapters.antigravity_acp.kit import AntigravityRoom
    from tests.adapters.antigravity_acp.peerconfig import PeerSettings

if importlib.util.find_spec("acp") is None:
    collect_ignore_glob = ["test_*.py"]


@pytest_asyncio.fixture(loop_scope="function")
async def open_peer(
    tmp_path: Path,
) -> AsyncIterator[Callable[..., Awaitable[AntigravityRoom]]]:
    from tests.adapters.antigravity_acp.runtime import (  # noqa: PLC0415 -- the peer imports the optional acp dependency
        open_room,
    )

    async with AsyncExitStack() as stack:
        opened: list[AntigravityRoom] = []

        async def create(
            settings: PeerSettings | None = None, **kwargs: Any
        ) -> AntigravityRoom:
            room = await stack.enter_async_context(
                open_room(tmp_path / f"peer-{len(opened)}", settings, **kwargs)
            )
            opened.append(room)
            return room

        yield create


@pytest_asyncio.fixture(loop_scope="function")
async def permission_control() -> AsyncIterator[PermissionControl]:
    from tests.adapters.antigravity_acp.control import (  # noqa: PLC0415 -- the peer imports the optional acp dependency
        PermissionControl,
    )
    from tests.adapters.antigravity_acp.peer import (  # noqa: PLC0415 -- the peer imports the optional acp dependency
        PEER_HOST,
    )

    with HeldPort(PEER_HOST) as held:
        control = PermissionControl(held.port)
        held.release()
        server = await asyncio.start_server(control.connected, PEER_HOST, control.port)
    try:
        yield control
    finally:
        server.close()
        await control.close()
        await server.wait_closed()
