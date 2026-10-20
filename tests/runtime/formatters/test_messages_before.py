from __future__ import annotations

from typing import Any, ClassVar

from band.runtime.formatters import (
    messages_before,
)


class TestMessagesBefore:
    """Bootstrap history must stop at the trigger: later entries are pending
    turns of their own, and replaying them exposes future requests and
    duplicates them when their own turn arrives."""

    MESSAGES: ClassVar[list[dict[str, Any]]] = [
        {"id": "m1", "content": "old one"},
        {"id": "m2", "content": "old two"},
        {"id": "trigger", "content": "the current message"},
        {"id": "m3", "content": "pending backlog"},
    ]

    def test_truncates_strictly_before_the_trigger(self):
        result = messages_before(self.MESSAGES, "trigger")
        assert [m["id"] for m in result] == [
            "m1",
            "m2",
        ], "the trigger and everything after it must not enter bootstrap history"

    def test_unknown_trigger_keeps_all(self):
        assert messages_before(self.MESSAGES, "not-there") == self.MESSAGES

    def test_trigger_first_returns_empty(self):
        assert messages_before(self.MESSAGES, "m1") == []
