"""One room's held connect or close never holds up another room."""

from __future__ import annotations

import asyncio

from tests.adapters.claude_sdk.fakecli import FakeClaude, Hold
from tests.adapters.claude_sdk.helpers import exchange
from tests.integrations.claude_sdk.sessionmanager.kit import (
    new_manager,
    thinking,
    thoughts,
)


class TestRoomIndependence:
    async def test_a_held_connect_or_close_never_holds_up_another_room(
        self,
        claude: FakeClaude,
    ) -> None:
        manager = new_manager()
        claude.script(thinking("b first"), thinking("a"), thinking("b again"))
        connecting = claude.connecting = Hold()
        opening = asyncio.create_task(exchange(manager.room("room-a"), "hi"))
        async with connecting:
            assert thoughts(await exchange(manager.room("room-b"), "hi")) == ["b first"]
        await opening

        closing = claude.closing = Hold()
        leaving = asyncio.create_task(manager.cleanup_session("room-a"))
        async with closing:
            assert thoughts(await exchange(manager.room("room-b"), "hi")) == ["b again"]
        await leaving
        await manager.stop()

        assert [session.alive for session in claude.sessions] == [False, False]
