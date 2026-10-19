"""A leave whose caller stops waiting still forgets the room once its owned
cleanup finishes, so a rejoined room may repeat an earlier reply."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapterConfig
from band.core.exceptions import CleanupIncompleteError
from tests.adapters.claude_sdk.helpers import REPORTING_BUDGET, ClaudeRoom, OpenRoom
from tests.adapters.hold import Hold
from tests.adapters.waiting import until


async def caller_cancelled(room: ClaudeRoom, closing: Hold) -> None:
    leaving = asyncio.create_task(room.leave())
    async with closing:
        leaving.cancel()
        await asyncio.wait({leaving})


async def report_timed_out(room: ClaudeRoom, closing: Hold) -> None:
    with pytest.raises(CleanupIncompleteError):
        await room.leave()
    async with closing:
        pass


class TestUnfinishedLeave:
    @pytest.mark.looptime
    @pytest.mark.parametrize("stop_waiting", [caller_cancelled, report_timed_out])
    async def test_an_unfinished_leave_forgets_what_the_room_said(
        self,
        claude_room: OpenRoom,
        stop_waiting: Callable[[ClaudeRoom, Hold], Awaitable[None]],
    ) -> None:
        room = await claude_room(ClaudeSDKAdapterConfig(retention=REPORTING_BUDGET))
        room.claude.script([room.model_reply("hello")], [room.model_reply("hello")])
        await room.send("say hello")
        closing = room.claude.closing = Hold()

        await stop_waiting(room, closing)
        await until(lambda: room.disposed, failure="the room's cleanup never finished")
        await room.send("say hello again")

        assert room.chat == ["hello", "hello"]
