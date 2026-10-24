"""Room lifecycle preparations for retained-write scenarios."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from tests.runtime.recovery.kit import Harness

RoomPreparation = Callable[[Harness], Awaitable[None]]


async def keep_original_room(room: Harness) -> None:
    """Keep the original execution context serving the room."""


async def replace_room(room: Harness) -> None:
    """Resume the room in a replacement execution context."""
    await room.replace_room()


ROOM_PREPARATIONS = [
    pytest.param(keep_original_room, id="original"),
    pytest.param(replace_room, id="replacement"),
]
