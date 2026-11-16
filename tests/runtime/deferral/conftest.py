"""Real room-runtime ownership for backend recovery scenarios."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from band.core.turn_lifecycle import BackendTurns
from band.platform.link import BandLink
from band.runtime.runtime import AgentRuntime
from tests.core.recordingcancel import RecordingCancel
from tests.runtime.deferral.kit import RecoveringAdapter
from tests.runtime.delivery.kit import RestPeer
from tests.runtime.helpers import (
    AGENT_ID,
    ROOM_ID,
    agent_hooks,
    no_periodic_wake_config,
    rest_client_over,
)


@pytest.fixture
async def recovering_runtime() -> AsyncIterator[
    tuple[AgentRuntime, BackendTurns[object], RecoveringAdapter, RestPeer]
]:
    turns = BackendTurns(RecordingCancel(), provider="test", requires_confirmation=True)
    adapter = RecoveringAdapter(turns)
    peer = RestPeer()
    async with rest_client_over(peer.answer) as rest:
        link = BandLink(agent_id=AGENT_ID, api_key="test-key")
        link.rest = rest
        runtime = AgentRuntime(
            link=link,
            agent_id=AGENT_ID,
            session_config=no_periodic_wake_config(),
            **agent_hooks(adapter),
        )
        try:
            yield runtime, turns, adapter, peer
        finally:
            await runtime.rooms.destroy(ROOM_ID)
            await turns.drain()
