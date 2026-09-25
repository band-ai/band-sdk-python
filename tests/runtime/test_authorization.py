"""Tests for is_sender_authorized (INT-1542)."""

from __future__ import annotations

from band.runtime.authorization import is_sender_authorized


class TestIsSenderAuthorized:
    def test_none_allows_anyone(self) -> None:
        assert is_sender_authorized("alice", None) is True
        assert is_sender_authorized(None, None) is True

    def test_member_of_a_non_empty_collection_is_authorized(self) -> None:
        assert is_sender_authorized("alice", {"alice", "bob"}) is True

    def test_non_member_of_a_non_empty_collection_is_not_authorized(self) -> None:
        assert is_sender_authorized("carol", {"alice", "bob"}) is False

    def test_empty_collection_authorizes_nobody(self) -> None:
        """The whole reason this helper exists: an empty (non-None)
        collection must not be treated as "no restriction"."""
        assert is_sender_authorized("alice", frozenset()) is False
        assert is_sender_authorized("alice", set()) is False

    def test_none_sender_id_is_not_authorized_by_a_non_empty_collection(self) -> None:
        assert is_sender_authorized(None, {"alice"}) is False
