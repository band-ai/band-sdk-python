from __future__ import annotations

from band.runtime.tools import matches_identifier


class TestMatchesIdentifier:
    """Tests for the matches_identifier helper."""

    def test_match_by_handle(self) -> None:
        entity = {"handle": "alice", "name": "Alice Smith", "id": "u-1"}
        assert matches_identifier(entity, "alice") is True

    def test_match_by_name(self) -> None:
        entity = {"handle": "alice", "name": "Alice Smith", "id": "u-1"}
        assert matches_identifier(entity, "Alice Smith") is True

    def test_match_by_id(self) -> None:
        entity = {"handle": "alice", "name": "Alice Smith", "id": "u-1"}
        assert matches_identifier(entity, "u-1") is True

    def test_case_insensitive(self) -> None:
        entity = {"handle": "Alice", "name": "ALICE SMITH", "id": "U-1"}
        assert matches_identifier(entity, "alice") is True
        assert matches_identifier(entity, "alice smith") is True
        assert matches_identifier(entity, "u-1") is True

    def test_no_match(self) -> None:
        entity = {"handle": "alice", "name": "Alice Smith", "id": "u-1"}
        assert matches_identifier(entity, "bob") is False

    def test_missing_fields(self) -> None:
        """Should handle entities with missing or None fields."""
        assert matches_identifier({"name": "Alice"}, "Alice") is True
        assert matches_identifier({"handle": None, "name": "Alice"}, "Alice") is True
        assert matches_identifier({}, "anything") is False

    def test_at_prefix_normalization(self) -> None:
        """@alice and alice should match regardless of which side has the prefix."""
        entity_with_at = {"handle": "@alice", "name": "Alice Smith", "id": "u-1"}
        entity_without_at = {"handle": "alice", "name": "Alice Smith", "id": "u-1"}

        # identifier has @, entity doesn't
        assert matches_identifier(entity_without_at, "@alice") is True
        # entity has @, identifier doesn't
        assert matches_identifier(entity_with_at, "alice") is True
        # both have @
        assert matches_identifier(entity_with_at, "@alice") is True
        # neither has @
        assert matches_identifier(entity_without_at, "alice") is True

    def test_empty_identifier(self) -> None:
        """Empty string should only match empty field values."""
        entity = {"handle": "alice", "name": "Alice", "id": "u-1"}
        assert matches_identifier(entity, "") is False
