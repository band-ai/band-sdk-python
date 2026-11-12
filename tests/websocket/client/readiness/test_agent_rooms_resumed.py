"""The agent rooms channel reports only rejoins after a gap."""

from __future__ import annotations

import asyncio

from band_sdk_core import AgentTopicKind

from band.client.streaming import RoomAddedPayload, RoomRemovedPayload
from tests.identifiers import AGENT_ID
from tests.websocket.client.readiness.kit import Timeline, readiness_session


async def ignore(payload: RoomAddedPayload | RoomRemovedPayload) -> None:
    return None


class TestAgentRoomsResumed:
    async def test_a_rejoin_after_a_crash_signals_but_the_initial_join_does_not(
        self, timeline: Timeline
    ) -> None:
        resumed: list[None] = []
        signalled = asyncio.Event()

        def on_resumed() -> None:
            resumed.append(None)
            signalled.set()

        async with readiness_session(timeline) as (server, client):
            await client.join_agent_rooms_channel(
                AGENT_ID, ignore, ignore, on_resumed=on_resumed
            )
            assert resumed == []

            await server.crash_channel(AgentTopicKind.Rooms.topic(AGENT_ID))
            await signalled.wait()
            assert resumed == [None]
            assert server.connection_count == 1
