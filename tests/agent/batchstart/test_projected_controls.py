"""Custom preprocessing shares one projection for ordinary inputs and controls."""

from __future__ import annotations

from band.agent import Agent
from tests.agent.batchstart.kit import (
    CONTROL_CONTENT,
    PROJECTED_PREFIX,
    ROOM_ID,
    SESSION,
    Platform,
    ProjectedControlAdapter,
    ProjectingPreprocessor,
    Turn,
)
from tests.scenarios.peer import AGENT_ID


class TestProjectedControls:
    async def test_a_transformed_control_is_handled_outside_the_ordinary_batch(
        self, platform: Platform
    ) -> None:
        peer, adapter = platform.peer, ProjectedControlAdapter()
        for content in ("first", "second", CONTROL_CONTENT, "third"):
            peer.post(ROOM_ID, content)
        agent = Agent.create(
            adapter,
            agent_id=AGENT_ID,
            api_key="test-key",
            ws_url=platform.server.url,
            rest_url="https://test.invalid",
            session_config=SESSION,
            preprocessor=ProjectingPreprocessor(),
        )

        async with agent:
            await peer.observations.until(
                lambda: all(
                    s == "processed" for s in peer.room(ROOM_ID).status.values()
                )
            )

        assert adapter.turns == [Turn(("m1", "m2", "m4"), bootstrap=True)]
        assert adapter.controls == [f"{PROJECTED_PREFIX}{CONTROL_CONTENT}"]
        assert adapter.contents == [
            tuple(f"{PROJECTED_PREFIX}{text}" for text in ("first", "second", "third"))
        ]
