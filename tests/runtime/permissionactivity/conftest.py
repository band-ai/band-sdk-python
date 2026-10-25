from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

import pytest

from band.platform.link import BandLink
from band.runtime.execution import ExecutionContext
from band.runtime.sessionconfig import SessionConfig
from tests.runtime.helpers import AGENT_ID, rest_client_over
from tests.runtime.permissionactivity.kit import HeldTurn, RoomActivityPlatform


@pytest.fixture
def permission_platform() -> RoomActivityPlatform:
    return RoomActivityPlatform()


@pytest.fixture
async def held_room(
    permission_platform: RoomActivityPlatform,
) -> AsyncIterator[Callable[[str], tuple[ExecutionContext, HeldTurn]]]:
    rooms: list[tuple[ExecutionContext, HeldTurn]] = []
    async with rest_client_over(permission_platform.respond) as rest:

        def create(room_id: str) -> tuple[ExecutionContext, HeldTurn]:
            handler = HeldTurn()
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            ctx = ExecutionContext(
                room_id,
                link,
                handler.handle,
                agent_id=AGENT_ID,
                config=SessionConfig(enable_context_hydration=False),
            )
            rooms.append((ctx, handler))
            return ctx, handler

        yield create
        for ctx, handler in rooms:
            handler.release.set()
            await ctx.stop()
            await asyncio.gather(
                *(worker.task for worker in handler.workers), return_exceptions=True
            )
