"""Report when the agent stops monitoring a room."""

from __future__ import annotations

from typing import Any

import pytest

from band.integrations.desktop_app.settings import (
    MAX_ROOM_EVENT_TIMEOUT_S,
)
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


class TestMonitoringHealth:
    """The agent cannot see whether it is still monitoring: the view's display
    loop ticks on regardless, so a room it stopped watching looks watched."""

    async def test_the_agent_is_told_when_its_own_loop_stopped(self, room: Any) -> None:
        clock = Clock()
        live = room([message("m-1", "2026-01-01T00:00:01Z")], clock=clock)
        await live.join()
        await live.monitor()

        clock.advance(live.tuning.band_room_event_timeout_s + STALE_GRACE_S + 1)
        seen_by_the_view = await live.monitor(caller=MonitorCaller.APP)

        assert seen_by_the_view["monitoring"]["stale"] is True
        assert RoomTool.MONITOR in seen_by_the_view["monitoring_notice"]
        assert "attention='user_first'" in seen_by_the_view["monitoring_notice"], (
            "a user-requested stop must be recorded as the mode switch, "
            "not fought by the resume order"
        )
        assert "not a stop order" in seen_by_the_view["monitoring_notice"], (
            "the switch is the user's to order: without this guard a stalled "
            "agent can take the exception on its own and silently unwatch the "
            "room"
        )

    async def test_the_views_own_ticks_do_not_stand_in_for_the_agents(
        self, room: Any
    ) -> None:
        """Otherwise the display loop alone would report a healthy room."""
        clock = Clock()
        live = room([message("m-1", "2026-01-01T00:00:01Z")], clock=clock)
        await live.join()
        await live.monitor()

        quantum = live.tuning.band_room_event_timeout_s
        for _ in range(int((STALE_GRACE_S + quantum) // quantum) + 2):
            clock.advance(quantum)
            watched = await live.monitor(caller=MonitorCaller.APP)

        assert watched["monitoring"]["stale"] is True

    async def test_a_long_quantum_loop_is_not_mistaken_for_a_stopped_one(
        self, room: Any
    ) -> None:
        """The agent picks its own quantum per call, so a limit read off the
        install default would call a healthy long-quantum loop stopped after
        a single wait."""
        clock = Clock()
        live = room([message("m-1", "2026-01-01T00:00:01Z")], clock=clock)
        await live.join()
        await live.monitor(timeout_seconds=MAX_ROOM_EVENT_TIMEOUT_S)

        clock.advance(MAX_ROOM_EVENT_TIMEOUT_S + 5)
        mid_wait = await live.monitor(caller=MonitorCaller.APP)
        clock.advance(STALE_GRACE_S)
        stopped = await live.monitor(caller=MonitorCaller.APP)

        assert mid_wait["monitoring"]["stale"] is False, (
            "one wait of the quantum it chose, plus its own latency"
        )
        assert stopped["monitoring"]["stale"] is True

    async def test_a_stopped_loop_is_reported_once_per_outage(self, room: Any) -> None:
        """The view goes on ticking, so a warning per tick would bury the log
        it exists to be found in."""
        clock = Clock()
        live = room([message("m-1", "2026-01-01T00:00:01Z")], clock=clock)
        await live.join()
        await live.monitor()
        clock.advance(live.tuning.band_room_event_timeout_s + STALE_GRACE_S + 1)

        stale = live.service.session.monitoring(ROOM_ID)
        reports = [
            live.service.session.claim_stale_report(ROOM_ID, stale) for _ in range(3)
        ]
        await live.monitor()
        again = live.service.session.claim_stale_report(
            ROOM_ID, live.service.session.monitoring(ROOM_ID)
        )

        assert reports == [True, False, False]
        assert again is False, (
            "the loop is running again, so there is nothing to report"
        )

    async def test_the_notice_clears_once_the_agent_monitors_again(
        self, room: Any
    ) -> None:
        """It rides every tick, so one that outlived its truth would nag on."""
        clock = Clock()
        live = room([message("m-1", "2026-01-01T00:00:01Z")], clock=clock)
        await live.join()
        await live.monitor()
        clock.advance(live.tuning.band_room_event_timeout_s + STALE_GRACE_S + 1)
        assert (await live.monitor(caller=MonitorCaller.APP))["monitoring_notice"]

        await live.monitor()

        assert (await live.monitor(caller=MonitorCaller.APP))["monitoring_notice"] == ""
