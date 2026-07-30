"""What the mounted view does when its host answers out of order.

These are the view's concurrency contracts — a room that changes under an
in-flight watch, a refresh overtaken by an event — and no static inspection of
the asset can reach them. ``roomview.mjs`` runs the shipped script against a
fake host and reports what it did.
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
    def test_an_overtaken_refresh_does_not_rewind_the_resume_cursor(
        self, behaviour: dict[str, Any]
    ) -> None:
        """A rewound cursor has the server re-read and redeliver what is shown."""
        result = behaviour["cursorRewind"]

        assert result["refreshAskedFrom"] < result["resumedFrom"]
        assert result["resumedFrom"] == "2026-01-01T00:00:09Z"

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


class TestWake:
    def test_a_wake_the_host_refused_is_not_offered_back(
        self, behaviour: dict[str, Any]
    ) -> None:
        """Desktop refuses an unactivated `ui/message` as a JSON-RPC error, not
        an `isError` result. Retrying that re-asks an answered question on every
        tick for the rest of the conversation."""
        result = behaviour["wakeRefusedByTheHost"]

        assert result["shapesTried"] == 2, "both content shapes are still probed"
        assert result["retryWakes"] == []


class TestWakeButton:
    def test_a_stopped_loop_becomes_the_users_one_click_repair(
        self, behaviour: dict[str, Any]
    ) -> None:
        """A context update cannot start a turn — the host defers it until the
        next user message — so the user is the only actor who can restart a
        stopped loop. The button turns their click into the `ui/message` that
        does, relaying the server-authored notice: the view writes no text."""
        result = behaviour["staleWakeButton"]

        assert result["hiddenWhileHealthy"] is True
        assert result["shownWhileStale"] is True
        assert "NOT monitoring" in result["wakeText"]
        assert result["hiddenAfterRecovery"] is True, (
            "a resumed loop must take its repair button with it"
        )


class TestOnDemand:
    def test_the_check_button_is_the_standing_way_in(
        self, behaviour: dict[str, Any]
    ) -> None:
        """No turn runs between the user's visits, so the widget is the inbox
        and one click — carrying the activation ui/message needs — is how the
        backlog gets a turn without composing a message."""
        result = behaviour["onDemandCheckButton"]

        assert result["buttonOffered"] is True
        assert (
            "Check room" in result["relayedText"]
            or "band_wait" in result["relayedText"]
        )
        assert result["diagnostics"].startswith("On demand · 1 waiting")


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
