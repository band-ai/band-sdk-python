"""The monitor loop: what a tick costs, and what it tells the agent."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from band.integrations.desktop_app.event_relay import RelayStatus, RoomEventBroker
from band.integrations.desktop_app.settings import (
    MAX_ROOM_EVENT_TIMEOUT_S,
    RoomViewTuning,
)
from band.integrations.desktop_app.tools import MonitorCaller, RoomTool
from tests.integrations.desktop_app.conftest import (
    DEAD,
    LIVE,
    ROOM_ID,
    TOM,
    Clock,
    ids,
    mentioned_message,
    message,
)

STALE_GRACE_S = RoomViewTuning().band_room_stale_grace_s


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


class TestViewInstances:
    """A remounted widget takes the room over; the old one retires itself."""

    async def test_a_newer_mount_supersedes_every_older_one(self, room: Any) -> None:
        live = room([message("m-1", "2026-01-01T00:00:01Z")])
        await live.join()

        first = await live.monitor(caller=MonitorCaller.APP, instance="widget-a")
        newer = await live.monitor(caller=MonitorCaller.APP, instance="widget-b")
        older = await live.monitor(caller=MonitorCaller.APP, instance="widget-a")

        assert first["superseded"] is False
        assert newer["superseded"] is False
        assert older["superseded"] is True, (
            "an already-known instance must never steal ownership back"
        )

    async def test_the_models_calls_never_retire_anything(self, room: Any) -> None:
        live = room([message("m-1", "2026-01-01T00:00:01Z")])
        await live.join()
        await live.monitor(caller=MonitorCaller.APP, instance="widget-a")

        model_tick = await live.monitor()
        still_owner = await live.monitor(caller=MonitorCaller.APP, instance="widget-a")

        assert model_tick["superseded"] is False
        assert still_owner["superseded"] is False

    async def test_a_takeover_reaches_the_wait_already_in_flight(
        self, room: Any
    ) -> None:
        """The old widget is usually parked on a wait when the new one mounts,
        so telling only its *next* call leaves both on screen for a tick."""
        live = room([message("m-1", "2026-01-01T00:00:01Z")])
        await live.join()
        first = await live.monitor(caller=MonitorCaller.APP, instance="widget-a")

        parked = asyncio.create_task(
            live.monitor(
                caller=MonitorCaller.APP,
                instance="widget-a",
                since=first["next_since"],
            )
        )
        await asyncio.sleep(0)
        takeover = asyncio.create_task(
            live.monitor(caller=MonitorCaller.APP, instance="widget-b")
        )
        await asyncio.sleep(0)
        await live.publish()

        assert (await parked)["superseded"] is True, (
            "the takeover happened while this call was parked on the wait"
        )
        assert (await takeover)["superseded"] is False


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

    async def test_a_quiet_tick_still_reads_because_own_posts_carry_no_event(
        self, room: Any
    ) -> None:
        """The platform does not echo the agent's own messages to its own
        socket, so a post made through band-mcp arrives only by reading.
        Observed live: with reads skipped on the event-stream's word, the
        agent's own posts never rendered, and the cursor advanced past them
        so they never appeared at all."""
        own = {**message("own-1", "2026-01-01T00:00:12Z"), "sender_id": TOM["id"]}
        live = room(
            [message("m-1", "2026-01-01T00:00:01Z")],
            [message("m-1", "2026-01-01T00:00:01Z")],
            [message("m-1", "2026-01-01T00:00:01Z"), own],
            transport=LIVE,
        )
        await live.join()
        first = await live.monitor()

        quiet = await live.monitor(since=first["next_since"])

        assert quiet["event_received"] is False, "no event announced the post"
        assert ids(quiet["messages"]) == ["own-1"]
        assert quiet["pending_requests"] == [], "an own post is never pending"

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
    async def test_model_and_view_receive_a_tied_timestamp_message(
        self, room: Any
    ) -> None:
        """Each monitor loop owns its cursor receipts after room opening."""
        at = "2026-01-01T00:00:01Z"
        first_message = message("m-1", at)
        late_message = mentioned_message("m-2", at)
        live = room(
            [first_message],
            [first_message, late_message],
        )

        opened = await live.read()
        model = await live.monitor(since=opened.resume_token)
        view = await live.monitor(
            since=opened.resume_token,
            caller=MonitorCaller.APP,
            instance="widget-a",
        )

        assert ids(model["messages"]) == ["m-2"]
        assert ids(model["pending_requests"]) == ["m-2"]
        assert ids(view["messages"]) == ["m-2"]

    async def test_a_later_message_sharing_the_cursor_timestamp_arrives_once(
        self, room: Any
    ) -> None:
        """Timestamp ties must not be lost or repeated across monitor ticks."""
        at = "2026-01-01T00:00:01Z"
        first_message = message("m-1", at)
        live = room(
            [first_message],
            [first_message, message("m-2", at)],
            tuning=RoomViewTuning(band_transcript_page_size=1),
        )

        first = await live.monitor()
        second = await live.monitor(since=first["next_since"])
        third = await live.monitor(since=second["next_since"])

        assert ids(first["messages"]) == ["m-1"]
        assert ids(second["messages"]) == ["m-2"]
        assert third["messages"] == []

    async def test_a_quiet_tick_repeats_the_cursor_rather_than_minting_one(
        self, room: Any
    ) -> None:
        """Only timestamps observed from Band may become the cursor; one from
        this machine's clock can run ahead of the platform and filter out
        what is still committing there."""
        live = room([message("m-1", "2026-01-01T00:00:01Z")], [], [])
        await live.join()

        first = await live.monitor(since="2026-01-01T00:00:09Z")
        second = await live.monitor(since=first["next_since"])

        assert first["next_since"] == "2026-01-01T00:00:09Z"
        assert second["next_since"] == first["next_since"]
        assert f"since={second['next_since']}" in second["summary_text"]

    async def test_a_message_behind_a_fast_local_clock_still_arrives(
        self, room: Any
    ) -> None:
        """The shape of the loss: a quiet tick on a machine whose clock ran
        ahead, then a message committing with an earlier platform timestamp.
        A locally minted cursor filtered it out of every later read."""
        clock = Clock(datetime(2026, 1, 2, tzinfo=timezone.utc))
        live = room(
            [message("m-1", "2026-01-01T00:00:01Z")],
            [message("m-1", "2026-01-01T00:00:01Z")],
            [message("m-1", "2026-01-01T00:00:01Z")],
            [
                message("m-1", "2026-01-01T00:00:01Z"),
                message("m-2", "2026-01-01T00:00:06Z"),
            ],
            clock=clock,
        )
        await live.join()

        first = await live.monitor()
        quiet = await live.monitor(since=first["next_since"])
        late = await live.monitor(since=quiet["next_since"])

        assert quiet["messages"] == []
        assert ids(late["messages"]) == ["m-2"]


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

    async def test_the_views_tick_does_not_consume_the_agents_report(
        self, room: Any
    ) -> None:
        """Reporting a room discharges it, and the view ticks every few
        seconds — but its summary never reaches the model, so a view tick that
        consumed the report would mean the agent is never told at all."""
        live = room(
            [],
            [],
            [],
            [],
            transport=RelayStatus(role="follower", rooms_added=[ROOM_ID, "room-2"]),
        )

        await live.monitor(caller=MonitorCaller.APP)
        told = await live.monitor()

        assert "room-2" in told["summary_text"]
