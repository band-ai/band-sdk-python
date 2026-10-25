"""Bootstrap history stops at the turn's earliest stored original."""

from __future__ import annotations

from typing import Any, ClassVar

from band.runtime.formatters import messages_before


def ids(messages: list[dict[str, Any]]) -> list[str]:
    return [message["id"] for message in messages]


class TestMessagesBefore:
    """Entries from the earliest original on are pending turns of their own:
    replaying them exposes future requests and duplicates them when their own
    turn arrives."""

    MESSAGES: ClassVar[list[dict[str, Any]]] = [
        {"id": "m1", "content": "old one"},
        {"id": "m2", "content": "old two"},
        {"id": "trigger", "content": "the current message"},
        {"id": "m3", "content": "pending backlog"},
    ]

    def test_the_prefix_ends_strictly_before_the_original(self) -> None:
        assert ids(messages_before(self.MESSAGES, {"trigger"})) == ["m1", "m2"]

    def test_the_earliest_stored_original_ends_the_prefix(self) -> None:
        assert ids(messages_before(self.MESSAGES, {"m3", "trigger"})) == ["m1", "m2"]

    def test_originals_that_are_not_stored_keep_everything(self) -> None:
        assert messages_before(self.MESSAGES, {"not-there"}) == self.MESSAGES

    def test_an_original_first_in_the_room_leaves_no_history(self) -> None:
        assert messages_before(self.MESSAGES, {"m1"}) == []
