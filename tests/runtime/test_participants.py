"""Tests for the participant projection/merge helpers.

The projection's exact key set is load-bearing: ``participants_changed()``
compares whole dicts, so a volatile field leaking through the projection
(e.g. ``status`` or ``last_seen``) would re-inject the roster every turn.
"""

from __future__ import annotations

from band.runtime.participants import merge_participant, participant_snapshot


class TestParticipantSnapshot:
    def test_projects_to_exact_field_set(self):
        snapshot = participant_snapshot(
            {
                "id": "1",
                "name": "Alice",
                "type": "User",
                "role": "owner",
                "status": "active",
                "last_seen": "now",
            }
        )
        assert set(snapshot.keys()) == {"id", "name", "type", "handle", "description"}

    def test_missing_fields_become_none(self):
        snapshot = participant_snapshot({"id": "1"})
        assert snapshot == {
            "id": "1",
            "name": None,
            "type": None,
            "handle": None,
            "description": None,
        }


class TestMergeParticipant:
    def test_none_never_erases_a_known_field(self):
        existing = participant_snapshot(
            {"id": "1", "name": "Alice", "type": "User", "description": "Billing"}
        )
        refreshed = merge_participant(
            existing, participant_snapshot({"id": "1", "name": "Alice", "type": "User"})
        )
        assert refreshed["description"] == "Billing"

    def test_empty_string_never_erases_a_known_field(self):
        """A source that serializes an unknown field as ``""`` rather than
        omitting the key (plausible for the participants-list endpoint) must
        not win the merge — the consumer (``build_participants_message``)
        treats both ``None`` and ``""`` as absent, so the merge has to match.
        """
        existing = participant_snapshot(
            {"id": "1", "name": "Alice", "type": "User", "description": "Billing"}
        )
        refreshed = merge_participant(
            existing,
            participant_snapshot(
                {"id": "1", "name": "Alice", "type": "User", "description": ""}
            ),
        )
        assert refreshed["description"] == "Billing"

    def test_fresh_values_win(self):
        existing = participant_snapshot({"id": "1", "name": "Old", "type": "User"})
        refreshed = merge_participant(
            existing,
            participant_snapshot({"id": "1", "name": "New", "type": "User"}),
        )
        assert refreshed["name"] == "New"

    def test_empty_existing_keeps_full_key_set(self):
        merged = merge_participant({}, participant_snapshot({"id": "1"}))
        assert set(merged.keys()) == {"id", "name", "type", "handle", "description"}
