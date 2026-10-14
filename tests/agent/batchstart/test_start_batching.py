"""Agent.start wires a batch-capable adapter into the room's scheduler."""

from __future__ import annotations

from band.agent import Agent
from tests.agent.batchstart.kit import (
    ROOM_ID,
    SESSION,
    Platform,
    QueuedAdapter,
    Turn,
)
from tests.runtime.batching.kit import CapacityAction
from tests.scenarios.peer import AGENT_ID


class TestStartBatching:
    async def test_the_started_agent_runs_one_turn_over_the_queue_through_its_capacity(
        self, platform: Platform
    ) -> None:
        peer, adapter = platform.peer, QueuedAdapter()
        for content in ("first", "second", "third"):
            peer.post(ROOM_ID, content)
        agent = Agent.create(
            adapter,
            agent_id=AGENT_ID,
            api_key="test-key",
            ws_url=platform.server.url,
            rest_url="https://test.invalid",
            session_config=SESSION,
        )

        async with agent:
            await peer.observations.until(lambda: len(adapter.turns) == 1)
            peer.post(ROOM_ID, "fourth")
            await peer.observations.until(lambda: len(adapter.turns) == 2)

        # The first accepted turn is still the bootstrap: the refused offer
        # committed no context, and the accepted one did.
        assert adapter.turns == [
            Turn(("m1", "m2", "m3"), bootstrap=True),
            Turn(("m4",), bootstrap=False),
        ]
        assert (adapter.capacity.registrations, adapter.capacity.actions()[-1]) == (
            1,
            CapacityAction.WITHDRAW,
        )
        assert set(peer.room(ROOM_ID).status.values()) == {"processed"}
