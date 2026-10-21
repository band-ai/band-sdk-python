"""Bridge control play nudge."""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock

from tests.bridge.conftest import FakeForwarder
from tests.bridge.kit import (
    build_runner,
    control_signal,
    drain_control_tasks,
    make_platform_message,
)


class TestControlPlayNudge:
    async def test_room_scope_play_nudges_next_message(self) -> None:
        runner, fwd, link = build_runner()
        link.get_next_message = AsyncMock(
            return_value=make_platform_message("m-a", "r1")
        )

        await runner._control.handle(control_signal("play", scope="room", room_id="r1"))
        await drain_control_tasks(runner)

        assert isinstance(fwd, FakeForwarder)
        ids = {(p.get("payload") or {}).get("id") for p in fwd.forwarded}
        assert ids == {"m-a"}

    async def test_agent_scope_play_nudges_all_rooms(self) -> None:
        runner, fwd, link = build_runner()
        link.rest.agent_api_chats.list_agent_chats = AsyncMock(
            return_value=MagicMock(data=[MagicMock(id="r1"), MagicMock(id="r2")])
        )
        link.get_next_message.side_effect = [
            make_platform_message("m-a", "r1"),
            make_platform_message("m-b", "r2"),
        ]

        await runner._control.handle(
            control_signal("play", scope="agent", room_id=None)
        )
        await drain_control_tasks(runner)

        assert isinstance(fwd, FakeForwarder)
        ids = {(p.get("payload") or {}).get("id") for p in fwd.forwarded}
        assert ids == {"m-a", "m-b"}

    async def test_play_with_no_backlog_forwards_nothing(self) -> None:
        runner, fwd, link = build_runner()
        link.get_next_message = AsyncMock(return_value=None)

        await runner._control.handle(control_signal("play", scope="room", room_id="r1"))
        await drain_control_tasks(runner)

        assert isinstance(fwd, FakeForwarder)
        assert fwd.forwarded == []

    async def test_play_nudge_does_not_block_receive_path(self) -> None:
        """play must return on the receive task without awaiting the /next +
        forward, so a following stop/interrupt isn't queued behind it."""
        started = asyncio.Event()

        async def slow_next(room_id: str) -> None:
            started.set()
            await asyncio.sleep(120)  # would block the receive task if awaited

        runner, _, link = build_runner()
        link.get_next_message = AsyncMock(side_effect=slow_next)

        # handle() returns promptly even though the nudge is still running.
        await asyncio.wait_for(
            runner._control.handle(control_signal("play", scope="room", room_id="r1")),
            timeout=1.0,
        )
        await started.wait()  # the nudge did start, in the background
        assert runner._control_tasks  # tracked for cancellation on close()

        for task in tuple(runner._control_tasks):
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(*runner._control_tasks)
