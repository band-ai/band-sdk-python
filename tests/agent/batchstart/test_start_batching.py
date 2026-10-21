"""Agent.start wires a batch-capable adapter into the room's scheduler."""

from __future__ import annotations

import pytest

from band.agent import Agent
from band.preprocessing.default import DefaultPreprocessor
from tests.agent.batchstart.kit import (
    PROJECTED_PREFIX,
    ROOM_ID,
    SESSION,
    BareBatchAdapter,
    IdentityPreprocessor,
    Platform,
    ProjectingPreprocessor,
    QueuedAdapter,
    Turn,
)
from tests.scenarios.peer import AGENT_ID


class TestStartBatching:
    async def test_a_bare_adapter_without_controls_receives_the_whole_queue(
        self, platform: Platform
    ) -> None:
        peer, adapter = platform.peer, BareBatchAdapter()
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
            await peer.observations.until(
                lambda: all(
                    s == "processed" for s in peer.room(ROOM_ID).status.values()
                )
            )

        assert adapter.turns == [("m1", "m2", "m3")]

    @pytest.mark.parametrize(
        "preprocessor",
        [DefaultPreprocessor, IdentityPreprocessor, ProjectingPreprocessor],
    )
    async def test_the_started_agent_retries_a_refused_queue_as_one_turn(
        self, platform: Platform, preprocessor: type[DefaultPreprocessor]
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
            preprocessor=preprocessor(),
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
        assert adapter.refused == [("m1", "m2", "m3")]
        prefix = PROJECTED_PREFIX if preprocessor is ProjectingPreprocessor else ""
        assert adapter.contents == [
            tuple(f"{prefix}{content}" for content in ("first", "second", "third")),
            (f"{prefix}fourth",),
        ]
        assert set(peer.room(ROOM_ID).status.values()) == {"processed"}
