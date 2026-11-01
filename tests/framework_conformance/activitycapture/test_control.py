"""Control observation surrounds the delegated handler in its real ordered lane."""

from __future__ import annotations

import asyncio

import pytest

from band.client.streaming import AgentControlPayload, ControlMode
from tests.e2e.baseline.toolkit.activity import ActivityCapture, ObservingBandLink
from tests.platform.activity.kit import ActivityPeer


class TestControlCapture:
    async def test_handler_failure_remains_observable_without_changing_lane_policy(
        self,
        observed_link: tuple[ActivityCapture, ObservingBandLink, ActivityPeer],
    ) -> None:
        capture, link, _ = observed_link
        failure = ValueError("Control handler failed")

        async def handler(payload: AgentControlPayload) -> None:
            raise failure

        payload = AgentControlPayload(
            agent_id="agent", mode=ControlMode.STOP, room_id="room", scope="room"
        )
        await link._apply_control(None, handler, payload)
        entered = await capture.control("room", since=0)
        with pytest.raises(ValueError) as captured:
            await capture.control_finished(entered)
        assert captured.value is failure

    async def test_cancelled_handler_cannot_complete_a_control_barrier(
        self,
        observed_link: tuple[ActivityCapture, ObservingBandLink, ActivityPeer],
    ) -> None:
        capture, link, _ = observed_link
        release = asyncio.Event()

        async def handler(payload: AgentControlPayload) -> None:
            await release.wait()

        payload = AgentControlPayload(
            agent_id="agent", mode=ControlMode.STOP, room_id="room", scope="room"
        )
        running = asyncio.create_task(link._apply_control(None, handler, payload))
        try:
            entered = await capture.control("room", since=0)
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running
            with pytest.raises(asyncio.CancelledError):
                await capture.control_finished(entered)
            assert running.cancelled()
        finally:
            release.set()
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    async def test_handler_entry_and_completion_are_distinct(
        self, observed_link: tuple[ActivityCapture, ObservingBandLink, ActivityPeer]
    ) -> None:
        capture, link, _ = observed_link
        release = asyncio.Event()
        effects: list[str] = []

        async def handler(payload: AgentControlPayload) -> None:
            await release.wait()
            effects.append(payload.room_id or "")

        payload = AgentControlPayload(
            agent_id="agent", mode=ControlMode.STOP, room_id="room", scope="room"
        )
        running = asyncio.create_task(link._apply_control(None, handler, payload))
        try:
            entered = await capture.control("room", since=0)
            assert not entered.completed.done()
            assert effects == []
            release.set()
            await capture.control_finished(entered)
            assert effects == ["room"]
        finally:
            release.set()
            await running
