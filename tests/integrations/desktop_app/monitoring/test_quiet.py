"""Keep quiet monitoring ticks complete and inexpensive."""

from __future__ import annotations

from typing import Any

import pytest

from tests.integrations.desktop_app.conftest import (
    DEAD,
    LIVE,
    TOM,
    ids,
    message,
)
from tests.toolkit.clock import binary_clock_resolution

# All I/O is in memory, so long-poll deadlines can advance virtually.
pytestmark = pytest.mark.looptime(resolution=binary_clock_resolution())


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
