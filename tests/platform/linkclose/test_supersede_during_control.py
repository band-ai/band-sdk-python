"""A ``supersede`` queued behind a still-running control signal on the shared
``agent_control`` topic is handled at once, before the 1001 close can
reconnect."""

from __future__ import annotations

import asyncio

import pytest
from websockets.frames import CloseCode

from band.client.streaming.client import AgentControlPayload, ControlMode, WireEvent
from band.core.exceptions import AgentDisconnectedError
from band.platform.event import WebSocketDisconnectedEvent
from band.testing import fake_phoenix_server
from tests.conftest import TERMINAL_SUPERSEDE
from tests.platform.linkclose.kit import (
    AGENT_ID,
    CONTROL_TOPIC,
    HANG_GUARD_S,
    SUPERSEDED,
    connected,
    make_link,
    running,
)

STOP_CONTROL = {
    "mode": ControlMode.STOP,
    "scope": "agent",
    "agent_id": AGENT_ID,
    "execution_id": None,
    "room_id": None,
    "correlation_id": "control-1",
}


class TestSupersedeDuringControl:
    async def test_a_supersede_is_handled_while_a_control_handler_runs(
        self,
    ) -> None:
        """A STOP can take seconds; the supersede behind it must not wait."""
        async with fake_phoenix_server() as server:
            link = make_link(server.url)
            control_entered = asyncio.Event()
            release_control = asyncio.Event()

            async def held_control(payload: AgentControlPayload) -> None:
                control_entered.set()
                await release_control.wait()

            link.on_control = held_control

            async with connected(link), running(link) as run:
                await server.push(CONTROL_TOPIC, WireEvent.AGENT_CONTROL, STOP_CONTROL)
                await asyncio.wait_for(control_entered.wait(), HANG_GUARD_S)
                await server.push(
                    CONTROL_TOPIC, WireEvent.SUPERSEDE, TERMINAL_SUPERSEDE
                )
                event = await asyncio.wait_for(anext(link), HANG_GUARD_S)
                await server.close_connection(code=CloseCode.GOING_AWAY)
                with pytest.raises(AgentDisconnectedError) as disconnected:
                    await asyncio.wait_for(run, HANG_GUARD_S)

                assert event == WebSocketDisconnectedEvent(payload=SUPERSEDED)
                assert disconnected.value.reason == SUPERSEDED
                assert server.connection_count == 1
