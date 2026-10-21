"""The retained event line keeps one copy per delivery and frees an id once it leaves."""

from __future__ import annotations

from band.platform.event import PlatformEvent
from band.runtime.originals import delivery_id
from band.runtime.retained import RetainedEvents
from tests.conftest import make_message_event, make_participant_added_event


def delivered(line: RetainedEvents) -> list[str | None]:
    return [delivery_id(event) for event in line]


class TestRetainedEvents:
    def test_a_repeated_delivery_is_held_once(self) -> None:
        line = RetainedEvents()

        line.add(make_message_event(msg_id="m1"))
        line.add(make_message_event(msg_id="m1"))

        assert delivered(line) == ["m1"]

    def test_a_removed_delivery_can_be_held_again(self) -> None:
        line = RetainedEvents()
        line.add(make_message_event(msg_id="m1"))
        line.add(make_message_event(msg_id="m2"))

        line.remove(lambda event: delivery_id(event) == "m1")
        line.add(make_message_event(msg_id="m1"))

        assert delivered(line) == ["m2", "m1"]

    def test_events_without_a_delivery_are_all_kept_in_order(self) -> None:
        line = RetainedEvents()
        events: list[PlatformEvent] = [
            make_participant_added_event(participant_id="a"),
            make_message_event(msg_id="m1"),
            make_participant_added_event(participant_id="a"),
        ]

        for event in events:
            line.add(event)

        assert list(line) == events
