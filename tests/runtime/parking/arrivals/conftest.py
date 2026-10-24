from __future__ import annotations

import pytest

from tests.runtime.parking.harness import RoomPeer


@pytest.fixture
def room_peer() -> RoomPeer:
    """A room with nothing queued: every original arrives over the WebSocket."""
    return RoomPeer()
