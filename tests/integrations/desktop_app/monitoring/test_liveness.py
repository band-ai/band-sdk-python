"""Request a remount when no live widget shows the room."""

from __future__ import annotations

from typing import Any

import pytest

from band.integrations.desktop_app.tools import MonitorCaller, RoomTool
from tests.integrations.desktop_app.conftest import (
    ROOM_ID,
    Clock,
    message,
)
from tests.integrations.desktop_app.monitoring.kit import STALE_GRACE_S
from tests.toolkit.clock import binary_clock_resolution

# All I/O is in memory, so long-poll deadlines can advance virtually.
pytestmark = pytest.mark.looptime(resolution=binary_clock_resolution())


class TestViewLiveness:
    """The mirror of monitoring health: the agent is told when no live widget
    shows the room, because only the agent can remount one — a Desktop
    restart kills widgets without a word to anyone."""

    async def test_a_room_with_no_view_anywhere_asks_for_a_remount(
        self, room: Any
    ) -> None:
        """Monitoring resumed after a restart works, but leaves the user
        watching a dead relic widget unless the agent is told to remount."""
        live = room([message("m-1", "2026-01-01T00:00:01Z")])

        tick = await live.monitor()

        assert RoomTool.SHOW in tick["summary_text"]

    async def test_a_freshly_granted_view_is_not_reported_missing(
        self, room: Any
    ) -> None:
        """The mount grant counts as the first tick, so the seconds between
        the join result and the widget's first own call never ask the model
        to remount a widget that is already coming up."""
        live = room([message("m-1", "2026-01-01T00:00:01Z")])
        await live.join()

        tick = await live.monitor()

        assert RoomTool.SHOW not in tick["summary_text"]

    async def test_a_widget_that_stopped_ticking_is_reported(self, room: Any) -> None:
        clock = Clock()
        live = room([message("m-1", "2026-01-01T00:00:01Z")], clock=clock)
        await live.join()
        await live.monitor(caller=MonitorCaller.APP, instance="widget-a")

        clock.advance(live.tuning.band_room_event_timeout_s + STALE_GRACE_S + 1)
        tick = await live.monitor()

        assert RoomTool.SHOW in tick["summary_text"]

    async def test_the_hint_clears_once_the_view_is_remounted(self, room: Any) -> None:
        clock = Clock()
        live = room([message("m-1", "2026-01-01T00:00:01Z")], clock=clock)
        await live.join()
        clock.advance(live.tuning.band_room_event_timeout_s + STALE_GRACE_S + 1)
        assert RoomTool.SHOW in (await live.monitor())["summary_text"]

        await live.invoke(RoomTool.SHOW, chat_id=ROOM_ID)

        assert RoomTool.SHOW not in (await live.monitor())["summary_text"]

    async def test_a_superseded_relics_tick_is_not_a_live_view(self, room: Any) -> None:
        """After a remount only the new widget is on screen; the old one's
        last tick must not vouch for a view the user cannot see."""
        clock = Clock()
        live = room([message("m-1", "2026-01-01T00:00:01Z")], clock=clock)
        await live.join()
        await live.monitor(caller=MonitorCaller.APP, instance="widget-a")
        await live.monitor(caller=MonitorCaller.APP, instance="widget-b")

        clock.advance(live.tuning.band_room_event_timeout_s + STALE_GRACE_S + 1)
        await live.monitor(caller=MonitorCaller.APP, instance="widget-a")
        tick = await live.monitor()

        assert RoomTool.SHOW in tick["summary_text"], (
            "the dead owner's room was vouched for by a widget the user lost"
        )
