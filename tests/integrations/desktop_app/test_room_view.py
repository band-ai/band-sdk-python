"""What the mounted view does when its host answers out of order.

These are the view's concurrency contracts — a room that changes under an
in-flight watch, a payload delivered late — and no static inspection of the
asset can reach them. ``roomview.mjs`` runs the shipped script against a fake
host and reports what it did.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any

import pytest

DRIVER = Path(__file__).parent / "roomview.mjs"


@pytest.fixture(scope="module")
def behaviour() -> dict[str, Any]:
    """Every scenario's result, from one run of the real view."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = files("band.integrations.desktop_app.assets") / "room-view.js"
    with as_file(script) as path:
        run = subprocess.run(
            [node, str(DRIVER), str(path)],
            capture_output=True,
            text=True,
        )
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


class TestRoomSwitch:
    def test_a_watch_answered_after_the_room_changed_is_dropped(
        self, behaviour: dict[str, Any]
    ) -> None:
        """Absorbing it drags the display, and the model's context, back to the
        room that was just left."""
        result = behaviour["staleRoomResult"]

        assert result["room"] == "room-b"
        assert result["contextUpdatesAfterSwitch"] == 0, (
            "the abandoned room's transcript reached the model's context"
        )


class TestCursor:
    def test_a_quiet_tick_still_advances_it(self, behaviour: dict[str, Any]) -> None:
        """The server advances the cursor on a quiet tick precisely so the next
        call differs; a view that ignored that would stall the loop."""
        assert behaviour["quietTick"]["resumedFrom"] == "2026-01-01T00:00:20Z"


class TestModelContext:
    def test_pending_work_is_the_servers_answer_relayed(
        self, behaviour: dict[str, Any]
    ) -> None:
        """Recomputing it in the view disagrees with the tool result the model
        is acting on — here the agent's own later message hides an open ask."""
        assert behaviour["pendingIsTheServers"]["pending"] == ["ask"]

    def test_the_view_authors_no_instruction_of_its_own(
        self, behaviour: dict[str, Any]
    ) -> None:
        context = behaviour["pendingIsTheServers"]["text"]

        assert context.startswith("briefing"), "the briefing is server-authored"
        assert "pending" not in context, (
            "what to do about pending work is prompts.py's to say"
        )


class TestOnDemand:
    def test_the_widget_shows_what_is_waiting(self, behaviour: dict[str, Any]) -> None:
        """The widget is a window, not a control: waiting mentions are counted
        for the user to see, and speaking to the agent is the way in."""
        assert behaviour["onDemandInbox"]["diagnostics"].startswith(
            "On demand · 1 waiting"
        )


class TestWatchTiming:
    def test_how_long_to_block_is_left_to_the_server(
        self, behaviour: dict[str, Any]
    ) -> None:
        """Otherwise BAND_ROOM_EVENT_TIMEOUT_S never reaches the app's loop."""
        assert "timeout_seconds" not in behaviour["watchTiming"]["arguments"]

    def test_the_display_loop_names_itself(self, behaviour: dict[str, Any]) -> None:
        """Unnamed, these ticks pass for the agent's own and hide that its loop
        stopped — the view keeps watching either way."""
        assert behaviour["watchTiming"]["caller"] == "app"


class TestAttentionSwitch:
    def test_a_mode_switch_drops_the_old_modes_briefing(
        self, behaviour: dict[str, Any]
    ) -> None:
        """Quiet ticks shed the briefing, so after a switch the cache holds the
        old mode's contract: left in place it re-orders the model into the loop
        the user just stopped — and user_first disarms the staleness notice
        that could have corrected it."""
        result = behaviour["attentionSwitch"]

        assert "Monitoring contract" not in result["context"], (
            "the old mode's briefing reached the model's context after the switch"
        )
        assert result["diagnostics"].startswith("On demand")


class TestMonitoringNotice:
    def test_it_reaches_the_model_and_clears_with_the_fault(
        self, behaviour: dict[str, Any]
    ) -> None:
        """A notice that outlived its truth would nag for the whole session."""
        result = behaviour["monitoringNotice"]

        assert result["warned"] is True
        assert result["recovered"] is False
        assert result["keptBriefing"] is True, (
            "clearing the notice must not drop the cached briefing with it"
        )


class TestPersistentWatchFailure:
    def test_a_watch_that_keeps_failing_backs_off_instead_of_hot_looping(
        self, behaviour: dict[str, Any]
    ) -> None:
        """A deleted room or a revoked key fails the wait immediately, not
        after its timeout — left unbounded that turns the 250ms happy-path
        restart into several tool calls a second, forever."""
        delays = behaviour["persistentWatchFailure"]["delays"]

        assert len(delays) > 1, "need at least two retries to see it grow"
        assert delays == sorted(delays), "each retry must wait at least as long"
        assert delays[1] > delays[0], "the second retry must back off, not repeat"

    def test_it_gives_up_rather_than_retrying_forever(
        self, behaviour: dict[str, Any]
    ) -> None:
        """Backing off slower is not enough on its own: a permanently dead
        room must not be polled for as long as Desktop stays open."""
        result = behaviour["persistentWatchFailure"]

        assert result["gaveUp"] is True
        assert result["status"].startswith("Live updates stopped")
