"""The ``claude_room`` fixture tears its server down on the clock it started on."""

from __future__ import annotations

import asyncio

import looptime
import pytest

from tests.adapters.claude_sdk.helpers import OpenRoom


class TestFixtureClock:
    @pytest.mark.parametrize(
        "virtual_clock",
        [
            pytest.param(False, marks=pytest.mark.looptime(False), id="real-clock"),
            pytest.param(True, marks=pytest.mark.looptime, id="virtual-clock"),
        ],
    )
    async def test_fixture_cleanup_keeps_the_clock_that_started_the_server(
        self,
        monkeypatch: pytest.MonkeyPatch,
        claude_room: OpenRoom,
        virtual_clock: bool,
    ) -> None:
        room = await claude_room()
        room.claude.script([room.model_reply("done")])
        await room.send("hello")
        loop = asyncio.get_running_loop()
        assert isinstance(loop, looptime.LoopTimeEventLoop)
        cleanup_all = room.adapter.cleanup_all

        async def cleanup_on_original_clock() -> None:
            assert loop.looptime_on == virtual_clock
            await cleanup_all()

        # Keep the real server cleanup, observing its clock during fixture teardown.
        monkeypatch.setattr(room.adapter, "cleanup_all", cleanup_on_original_clock)
