"""OneShotInvoker: resolving a sender's display name."""

from __future__ import annotations

from band.runtime.oneshot import (
    _lookup_sender_name,
)


class TestLookupSenderName:
    def test_finds_by_id(self) -> None:
        participants = [
            {"id": "u1", "name": "Alice"},
            {"id": "u2", "name": "Bob"},
        ]
        assert _lookup_sender_name(participants, "u2") == "Bob"

    def test_returns_none_when_not_found(self) -> None:
        assert _lookup_sender_name([{"id": "x", "name": "x"}], "y") is None

    def test_returns_none_for_empty_sender_id(self) -> None:
        assert _lookup_sender_name([{"id": "x"}], None) is None
        assert _lookup_sender_name([{"id": "x"}], "") is None
