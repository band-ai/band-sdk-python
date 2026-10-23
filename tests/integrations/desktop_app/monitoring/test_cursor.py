"""Preserve message delivery across monitoring cursor boundaries."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from band.integrations.desktop_app.settings import (
    RoomViewTuning,
)
from band.integrations.desktop_app.tools import MonitorCaller
from tests.integrations.desktop_app.conftest import (
    Clock,
    ids,
    mentioned_message,
    message,
)
from tests.toolkit.clock import binary_clock_resolution

# All I/O is in memory, so long-poll deadlines can advance virtually.
pytestmark = pytest.mark.looptime(resolution=binary_clock_resolution())


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
        clock = Clock(datetime(2026, 1, 2, tzinfo=UTC))
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
