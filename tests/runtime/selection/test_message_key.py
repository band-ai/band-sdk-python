from __future__ import annotations

from datetime import UTC, datetime

import pytest

from band.platform.event import MessageEvent
from band.runtime.selection import MessageKey
from tests.conftest import make_message_event


class TestMessageKey:
    def test_orders_by_insertion_time_then_id(self) -> None:
        events = [
            make_message_event(msg_id="b", inserted_at="2024-01-01T00:00:02Z"),
            make_message_event(msg_id="z", inserted_at="2024-01-01T00:00:01Z"),
            make_message_event(msg_id="a", inserted_at="2024-01-01T00:00:02Z"),
        ]

        ordered = sorted(events, key=MessageKey.of_event)

        assert [e.payload.id for e in ordered if e.payload] == ["z", "a", "b"]

    def test_a_naive_server_timestamp_is_taken_as_utc(self) -> None:
        event = make_message_event(msg_id="a", inserted_at="2024-01-01T00:00:01")

        assert MessageKey.of_event(event).inserted_at == datetime(
            2024, 1, 1, 0, 0, 1, tzinfo=UTC
        )

    def test_an_event_without_a_payload_has_no_position(self) -> None:
        with pytest.raises(ValueError, match="no delivery"):
            MessageKey.of_event(MessageEvent(room_id="room-123", payload=None))
