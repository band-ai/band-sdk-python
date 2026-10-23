"""Report degraded transport and activity in other rooms."""

from __future__ import annotations

from typing import Any

import pytest

from band.integrations.desktop_app.event_relay import RelayStatus
from tests.integrations.desktop_app.conftest import (
    DEAD,
    ROOM_ID,
)
from tests.toolkit.clock import binary_clock_resolution

# All I/O is in memory, so long-poll deadlines can advance virtually.
pytestmark = pytest.mark.looptime(resolution=binary_clock_resolution())


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
