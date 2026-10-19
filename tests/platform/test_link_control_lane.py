"""BandLink applies control signals one at a time, in arrival order, off the
``agent_control`` topic processor; one failed signal never blocks the next."""

from __future__ import annotations

import asyncio
import logging

import pytest

from band.client.streaming import AgentControlPayload
from band.platform.link import BandLink


@pytest.fixture
def link() -> BandLink:
    return BandLink(
        agent_id="agent-123", api_key="k", ws_url="wss://x/ws", rest_url="https://x"
    )


class TestControlLane:
    async def test_a_signal_waits_for_the_one_before_it(self, link: BandLink):
        """A PLAY behind a STOP that is still draining applies after it."""
        stop = AgentControlPayload(mode="stop", scope="agent", agent_id="agent-123")
        play = AgentControlPayload(mode="play", scope="agent", agent_id="agent-123")
        applied: list[AgentControlPayload] = []
        stop_entered = asyncio.Event()
        release_stop = asyncio.Event()
        play_applied = asyncio.Event()

        async def hook(payload: AgentControlPayload) -> None:
            if payload is stop:
                stop_entered.set()
                await release_stop.wait()
            applied.append(payload)
            if payload is play:
                play_applied.set()

        link.on_control = hook
        await link._on_control(stop)
        await asyncio.wait_for(stop_entered.wait(), timeout=5.0)
        await link._on_control(play)
        release_stop.set()
        await asyncio.wait_for(play_applied.wait(), timeout=5.0)

        assert applied == [stop, play]

    async def test_a_failed_signal_is_logged_and_the_next_one_still_applies(
        self, link: BandLink, caplog: pytest.LogCaptureFixture
    ):
        stop = AgentControlPayload(
            mode="stop", scope="agent", agent_id="agent-123", correlation_id="stop-1"
        )
        play = AgentControlPayload(mode="play", scope="agent", agent_id="agent-123")
        play_applied = asyncio.Event()

        async def hook(payload: AgentControlPayload) -> None:
            if payload is stop:
                raise RuntimeError("adapter cancel failed")
            play_applied.set()

        link.on_control = hook
        with caplog.at_level(logging.ERROR, logger="band.platform.link"):
            await link._on_control(stop)
            await link._on_control(play)
            await asyncio.wait_for(play_applied.wait(), timeout=5.0)

        assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
            (
                logging.ERROR,
                "agent.control stop failed (correlation_id=stop-1 room_id=None)",
            )
        ]
