"""``disconnect()`` cancels every control signal not yet finished, the running
one and those queued behind it, and leaves applied ones alone."""

from __future__ import annotations

import asyncio
import logging

import pytest

from band.client.streaming import AgentControlPayload, ControlMode
from band.testing import fake_phoenix_server
from tests.platform.linkclose.kit import (
    HANG_GUARD_S,
    agent_control,
    connected,
    control_cancellations,
    make_link,
)

LINK_LOGGER = "band.platform.link"


class TestDisconnectCancelsControl:
    async def test_a_signal_queued_behind_a_running_one_never_applies(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        stop = agent_control(ControlMode.STOP, "stop-1")
        interrupt = agent_control(ControlMode.INTERRUPT, "interrupt-1")
        story: list[str] = []
        stop_entered = asyncio.Event()

        async def hook(payload: AgentControlPayload) -> None:
            story.append(f"{payload.mode} applying")
            stop_entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                story.append(f"{payload.mode} cancelled")
                raise

        async with fake_phoenix_server() as server:
            link = make_link(server.url)
            link.on_control = hook
            async with connected(link):
                await link._on_control(stop)
                await asyncio.wait_for(stop_entered.wait(), HANG_GUARD_S)
                await link._on_control(interrupt)

                with caplog.at_level(logging.INFO, logger=LINK_LOGGER):
                    await link.disconnect()

        assert story == ["stop applying", "stop cancelled"]
        assert control_cancellations(caplog.records) == [
            (
                "Cancelling 2 unfinished control signal(s) on disconnect: "
                "agent.control stop correlation_id=stop-1, "
                "agent.control interrupt correlation_id=interrupt-1"
            )
        ]

    async def test_an_applied_signal_leaves_nothing_to_cancel(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        applied = asyncio.Event()

        async def hook(payload: AgentControlPayload) -> None:
            applied.set()

        async with fake_phoenix_server() as server:
            link = make_link(server.url)
            link.on_control = hook
            async with connected(link):
                await link._on_control(agent_control(ControlMode.PLAY, "play-1"))
                await asyncio.wait_for(applied.wait(), HANG_GUARD_S)

                with caplog.at_level(logging.INFO, logger=LINK_LOGGER):
                    await link.disconnect()

        assert control_cancellations(caplog.records) == []
