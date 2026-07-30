"""The monitor loop: what a tick costs, and what it tells the agent."""

from __future__ import annotations

import asyncio
from typing import Any

from band.integrations.desktop_app.event_relay import RelayStatus, RoomEventBroker
from band.integrations.desktop_app.service import STALE_AFTER_TICKS
from band.integrations.desktop_app.tools import MonitorCaller, RoomTool
from tests.integrations.desktop_app.conftest import (
    DEAD,
    LIVE,
    ROOM_ID,
    Clock,
    ids,
    mentioned_message,
    message,
)


class TestMonitoringHealth:
    """The agent cannot see whether it is still monitoring: the view's display
    loop ticks on regardless, so a room it stopped watching looks watched."""

    async def test_the_agent_is_told_when_its_own_loop_stopped(self, room: Any) -> None:
        clock = Clock()
        live = room([message("m-1", "2026-01-01T00:00:01Z")], clock=clock)
        await live.join()
        await live.monitor()

        clock.advance(live.tuning.band_room_event_timeout_s * STALE_AFTER_TICKS + 1)
        seen_by_the_view = await live.monitor(caller=MonitorCaller.APP)

        assert seen_by_the_view["monitoring"]["stale"] is True
        assert RoomTool.MONITOR in seen_by_the_view["monitoring_notice"]

    async def test_the_views_own_ticks_do_not_stand_in_for_the_agents(
        self, room: Any
    ) -> None:
        """Otherwise the display loop alone would report a healthy room."""
        clock = Clock()
        live = room([message("m-1", "2026-01-01T00:00:01Z")], clock=clock)
        await live.join()
        await live.monitor()

        for _ in range(4):
            clock.advance(live.tuning.band_room_event_timeout_s)
            watched = await live.monitor(caller=MonitorCaller.APP)

        assert watched["monitoring"]["stale"] is True

    async def test_the_notice_clears_once_the_agent_monitors_again(
        self, room: Any
    ) -> None:
        """It rides every tick, so one that outlived its truth would nag on."""
        clock = Clock()
        live = room([message("m-1", "2026-01-01T00:00:01Z")], clock=clock)
        await live.join()
        await live.monitor()
        clock.advance(live.tuning.band_room_event_timeout_s * STALE_AFTER_TICKS + 1)
        assert (await live.monitor(caller=MonitorCaller.APP))["monitoring_notice"]

        await live.monitor()

        assert (await live.monitor(caller=MonitorCaller.APP))["monitoring_notice"] == ""


class TestWaking:
    async def test_the_broker_releases_everyone_waiting_on_a_room(self) -> None:
        broker = RoomEventBroker()
        waiter = asyncio.create_task(
            broker.wait(
                ROOM_ID, after_version=broker.version(ROOM_ID), timeout_seconds=1
            )
        )

        await broker.publish(ROOM_ID)

        assert await waiter is True

    async def test_an_event_mid_wait_hydrates_the_new_context(self, room: Any) -> None:
        live = room([], [mentioned_message("req-1", "2026-01-01T00:00:03Z")])

        waiting = asyncio.create_task(live.wait(since="2026-01-01T00:00:02Z"))
        await asyncio.sleep(0)
        await live.publish()
        result = await waiting

        assert result.event_received is True
        assert ids(result.pending_requests) == ["req-1"]

    async def test_a_live_mention_tells_the_model_to_answer_and_resume(
        self, room: Any
    ) -> None:
        live = room(
            [message("m-1", "2026-01-01T00:00:01Z")],
            [mentioned_message("req-1", "2026-01-01T00:00:03Z")],
        )
        await live.join()

        tick = await live.monitor()

        assert tick["pending_requests"][0]["id"] == "req-1"
        assert "Answer them in the room now" in tick["summary_text"]
        assert RoomTool.MONITOR in tick["summary_text"], (
            "every tick must point at the next one or the loop stops"
        )


class TestQuietTicks:
    """Quiet ticks repeat every few seconds, so they must stay cheap."""

    async def test_a_quiet_tick_omits_the_roster_and_briefing(self, room: Any) -> None:
        live = room([message("m-1", "2026-01-01T00:00:01Z")], [])
        await live.join()

        quiet = await live.monitor(since="2026-01-01T00:00:09Z")

        assert quiet["messages"] == []
        assert quiet["participants"] == []
        assert quiet["role_briefing"] == ""
        assert "Room quiet." in quiet["summary_text"]

    async def test_a_quiet_tick_on_a_live_socket_reads_no_rest(self, room: Any) -> None:
        """The broker's version already proves nothing changed; reading is waste."""
        live = room([message("m-1", "2026-01-01T00:00:01Z")], [], transport=LIVE)
        await live.join()
        first = await live.monitor()
        reads_after_first = live.rest_calls

        second = await live.monitor(since=first["next_since"])

        assert live.rest_calls == reads_after_first, (
            "a proven-current read was repeated"
        )
        assert second["messages"] == []

    async def test_a_dead_socket_reads_every_tick(self, room: Any) -> None:
        """With no live socket, polling is the only delivery and must not skip."""
        live = room([message("m-1", "2026-01-01T00:00:01Z")], [], [], transport=DEAD)
        await live.join()
        first = await live.monitor()
        reads_after_first = live.rest_calls

        await live.monitor(since=first["next_since"])

        assert live.rest_calls > reads_after_first

    async def test_a_caller_behind_the_last_read_is_never_skipped(
        self, room: Any
    ) -> None:
        """A cursor older than what the last read saw may still owe messages."""
        seen = message("m-1", "2026-01-01T00:00:05Z")
        live = room([seen], [seen], transport=LIVE)
        await live.monitor()
        reads_before = live.rest_calls

        behind = await live.monitor(since="2026-01-01T00:00:01Z")

        assert live.rest_calls > reads_before
        assert ids(behind["messages"]) == ["m-1"]


class TestCursor:
    async def test_it_advances_even_when_nothing_happened(self, room: Any) -> None:
        """Identical repeated calls look like duplicates to a dedup optimizer."""
        live = room([message("m-1", "2026-01-01T00:00:01Z")], [], [])
        await live.join()

        first = await live.monitor(since="2026-01-01T00:00:09Z")
        second = await live.monitor(since=first["next_since"])

        assert second["next_since"] > first["next_since"] > "2026-01-01T00:00:09"
        assert f"since={second['next_since']}" in second["summary_text"]

    async def test_it_never_passes_a_message_that_landed_mid_read(
        self, room: Any
    ) -> None:
        """A quiet read may only advance to before it started, never to now."""
        quiet = await room([], []).monitor()

        assert quiet["next_since"] < quiet["refreshed_at"]


class TestTransportReporting:
    async def test_a_degraded_transport_is_reported_to_the_agent(
        self, room: Any
    ) -> None:
        """A dead WebSocket silently downgrades monitoring to REST latency."""
        quiet = await room([], [], transport=DEAD).monitor()

        assert "WebSocket is down" in quiet["summary_text"]

    async def test_another_room_wanting_the_agent_is_reported_once(
        self, room: Any
    ) -> None:
        """The agent should be able to say another room now expects it."""
        live = room(
            [],
            [],
            [],
            [],
            transport=RelayStatus(role="follower", rooms_added=[ROOM_ID, "room-2"]),
        )

        first = await live.monitor()
        again = await live.monitor(since=first["next_since"])

        elsewhere = first["summary_text"].split("Band room(s) ")[1].split(".")[0]
        assert elsewhere == "room-2", "the watched room is not somewhere else"
        assert "room-2" not in again["summary_text"], "reported once, not every tick"
