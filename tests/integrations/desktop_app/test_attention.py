"""Whose attention the room gets first, and what each mode disarms.

User-first inverts the room-first loop: no turn is held, the user is answered
instantly, and the room is swept at turn starts. Everything the loop's health
machinery assumes — staleness and its notice — is intended to be off in that
mode, or it nags about a state the user chose.
"""

from __future__ import annotations

from typing import Any

from band.integrations.desktop_app.service import STALE_GRACE_S
from band.integrations.desktop_app.tools import AttentionMode, MonitorCaller
from tests.integrations.desktop_app.conftest import (
    ROOM_ID,
    Clock,
    ids,
    mentioned_message,
    message,
)


class TestContract:
    async def test_each_mode_briefs_only_its_own_contract(self, room: Any) -> None:
        """A briefing describing both behaviours gets blended; the other mode
        may appear only as the single line naming the way into it."""
        watched = await room([message("m-1", "2026-01-01T00:00:01Z")]).join()
        on_demand = await room([message("m-1", "2026-01-01T00:00:01Z")]).join(
            attention=AttentionMode.USER_FIRST
        )

        assert "Do not end your turn" in watched["role_briefing"]
        assert "sweep" not in watched["role_briefing"].split("attention=")[0].lower()
        assert "Do not end your turn" not in on_demand["role_briefing"]
        assert "At the start of every turn" in on_demand["role_briefing"]

    async def test_join_summary_tells_the_mode_apart(self, room: Any) -> None:
        on_demand = await room(
            [mentioned_message("req-1", "2026-01-01T00:00:01Z")]
        ).join(attention=AttentionMode.USER_FIRST)

        assert "Ask the user once whether" in on_demand["summary_text"]
        assert "Start monitoring" not in on_demand["summary_text"]

    async def test_a_sweep_is_told_not_to_keep_looping(self, room: Any) -> None:
        live = room([message("m-1", "2026-01-01T00:00:01Z")])
        await live.join(attention=AttentionMode.USER_FIRST)

        sweep = await live.monitor()

        assert "end your turn" in sweep["summary_text"]
        assert "to keep monitoring" not in sweep["summary_text"]
        assert f"since={sweep['next_since']}" in sweep["summary_text"], (
            "the next turn's sweep still needs its cursor"
        )


class TestDisarmedMachinery:
    async def test_staleness_never_fires_in_user_first(self, room: Any) -> None:
        """Not monitoring is the chosen state, not an outage."""
        clock = Clock()
        live = room([message("m-1", "2026-01-01T00:00:01Z")], clock=clock)
        await live.join(attention=AttentionMode.USER_FIRST)
        await live.monitor()

        clock.advance(live.tuning.band_room_event_timeout_s + STALE_GRACE_S + 100)
        quiet = await live.monitor(caller=MonitorCaller.APP)

        assert quiet["monitoring"]["stale"] is False
        assert quiet["monitoring_notice"] == ""

    async def test_a_mention_stays_pending_for_the_next_sweep(self, room: Any) -> None:
        """Nothing can start a turn while the user is away, so the mention's
        only path is to wait — counted in the view — for the next sweep."""
        live = room(
            [message("m-1", "2026-01-01T00:00:01Z")],
            [mentioned_message("req-1", "2026-01-01T00:00:03Z")],
        )
        await live.join(attention=AttentionMode.USER_FIRST)

        seen_by_view = await live.monitor(caller=MonitorCaller.APP)
        swept = await live.monitor(since=None)

        assert ids(seen_by_view["pending_requests"]) == ["req-1"]
        assert ids(swept["pending_requests"]) == ["req-1"], (
            "the view's ticks must not consume what the sweep is owed"
        )


class TestSwitching:
    async def test_the_agent_switches_modes_mid_conversation(self, room: Any) -> None:
        """The switch takes effect in the same reply that performed it."""
        clock = Clock()
        live = room([message("m-1", "2026-01-01T00:00:01Z")], clock=clock)
        await live.join()

        switched = await live.monitor(attention=AttentionMode.USER_FIRST)
        clock.advance(live.tuning.band_room_event_timeout_s + STALE_GRACE_S + 100)
        idle = await live.monitor(caller=MonitorCaller.APP)
        resumed = await live.monitor(attention=AttentionMode.ROOM_FIRST)

        assert switched["attention"] == AttentionMode.USER_FIRST
        assert "end your turn" in switched["summary_text"]
        assert idle["monitoring_notice"] == "", "idle was the chosen state"
        assert resumed["attention"] == AttentionMode.ROOM_FIRST
        assert "to keep monitoring" in resumed["summary_text"]

    async def test_switching_back_arms_staleness_freshly(self, room: Any) -> None:
        """The idle gap accrued in user-first must not fire the notice the
        moment the loop is re-armed: the switching call is itself the tick."""
        clock = Clock()
        live = room([message("m-1", "2026-01-01T00:00:01Z")], clock=clock)
        await live.join(attention=AttentionMode.USER_FIRST)
        await live.monitor()
        clock.advance(3_600)

        await live.monitor(attention=AttentionMode.ROOM_FIRST)
        armed = await live.monitor(caller=MonitorCaller.APP)

        assert armed["monitoring"]["stale"] is False

    async def test_the_view_cannot_flip_the_mode(self, room: Any) -> None:
        """The mode is the user's choice relayed through the model; the app's
        display loop calls the same tool and must not carry that authority."""
        live = room([message("m-1", "2026-01-01T00:00:01Z")])
        await live.join()

        hijack = await live.monitor(
            caller=MonitorCaller.APP, attention=AttentionMode.USER_FIRST
        )

        assert hijack["attention"] == AttentionMode.ROOM_FIRST
        assert live.service.attention(ROOM_ID) is AttentionMode.ROOM_FIRST
