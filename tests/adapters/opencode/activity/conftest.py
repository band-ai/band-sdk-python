"""Confirmed turn startup and cleanup for native activity scenarios."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import pytest

from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
from band.testing import FakeAgentTools
from band.testing.activity import ActivityRecorder
from tests.adapters.hold import Hold
from tests.adapters.opencode.activity.kit import SESSION, ActivityRoom, OpenActivityRoom
from tests.adapters.opencode.helpers import (
    FakeOpencodeClient,
    HeldPermissionReplyClient,
    event_session_idle,
    room_turn,
    run_single_turn,
    turn_finished,
)


@pytest.fixture
def activity_room(
    make_adapter: Callable[..., OpencodeAdapter], tools: FakeAgentTools
) -> OpenActivityRoom:
    @asynccontextmanager
    async def open_room(
        client_type: type[FakeOpencodeClient] = FakeOpencodeClient,
        *,
        config: OpencodeAdapterConfig | None = None,
    ) -> AsyncIterator[ActivityRoom]:
        submitted = Hold()
        client = client_type(prompt_event_sequences=[[submitted.wait]])
        adapter = make_adapter(client, config=config, emit=())
        recorder = ActivityRecorder()
        recorder.observing(tools)
        delivery = asyncio.create_task(run_single_turn(adapter, tools))
        try:
            await submitted.arrived()
            submitted.released.set()
            turn = await room_turn(adapter)
            try:
                yield ActivityRoom(adapter, client, tools, recorder)
            finally:
                if isinstance(client, HeldPermissionReplyClient):
                    client.release_reply.set()
                await client.push_event(event_session_idle(SESSION))
                await delivery
                await turn_finished(turn)
        finally:
            submitted.released.set()
            if not delivery.done():
                delivery.cancel()
            await asyncio.gather(delivery, return_exceptions=True)

    return open_room
