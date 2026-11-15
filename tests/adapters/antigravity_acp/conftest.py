"""Start and close real stdio ACP peers in per-test directories."""

from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio

from tests.framework_conformance.turnprobes import ROOM_ID
from tests.ports import HeldPort

if TYPE_CHECKING:
    from tests.adapters.antigravity_acp.control import PeerControl
    from tests.adapters.antigravity_acp.kit import AntigravityRoom
    from tests.adapters.antigravity_acp.peerconfig import PeerSettings
    from tests.integrations.acp.acp_toolkit.agent import Gate

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
async def peer_control() -> AsyncIterator[PeerControl]:
    from tests.adapters.antigravity_acp.control import (  # noqa: PLC0415 -- the peer imports the optional acp dependency
        PeerControl,
    )
    from tests.adapters.antigravity_acp.peer import (  # noqa: PLC0415 -- the peer imports the optional acp dependency
        PEER_HOST,
    )

    with HeldPort(PEER_HOST) as held:
        control = PeerControl(held.port)
        held.release()
        server = await asyncio.start_server(control.connected, PEER_HOST, control.port)
    try:
        yield control
    finally:
        server.close()
        await control.close()
        await server.wait_closed()


@pytest.fixture
def hold_native_cancel(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Callable[[AntigravityRoom], Awaitable[Gate]]:
    from tests.integrations.acp.acp_toolkit.agent import (  # noqa: PLC0415 -- the peer imports the optional acp dependency
        Gate,
    )

    async def hold(peer: AntigravityRoom) -> Gate:
        handle = peer.adapter.backend_turns.active_handle(ROOM_ID)
        if handle is None:
            raise RuntimeError("hold_native_cancel requires a running native prompt")
        connection = await handle.runtime.ensure_connection()
        cancel = connection.cancel
        gate = Gate()
        request.addfinalizer(gate.release.set)

        async def held_cancel(session_id: str, **kwargs: Any) -> None:
            await gate.hold()
            await cancel(session_id, **kwargs)

        # ACP cancellation is a notification; only a gate at its sender holds stop pending.
        monkeypatch.setattr(connection, "cancel", held_cancel)
        return gate

    return hold
