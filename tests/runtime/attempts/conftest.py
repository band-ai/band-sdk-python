from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from band.runtime.sessionconfig import SessionConfig
from tests.runtime.recovery.kit import (
    Ending,
    Harness,
    RoomPeer,
    ScriptedAdapter,
    Shape,
    wired_harness,
)

EXECUTION_BUDGET = 3


@pytest.fixture
async def retrying_room() -> AsyncIterator[Harness]:
    peer = RoomPeer()
    adapter = ScriptedAdapter()
    adapter.ending = Ending.RAISES
    config = SessionConfig(
        max_message_retries=EXECUTION_BUDGET,
        enable_context_hydration=False,
        enable_working_state=False,
    )
    async with peer.client() as rest:
        room = wired_harness(
            peer=peer, adapter=adapter, rest=rest, shape=Shape.OFF, config=config
        )
        try:
            yield room
        finally:
            await room.runtime.stop()
