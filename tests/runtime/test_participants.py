"""Tests for the participant snapshot projection helper.

The projection's exact key set is load-bearing: ``participants_changed()``
compares whole dicts, so a volatile field leaking through the projection
(e.g. ``status`` or ``last_seen``) would re-inject the roster every turn.

Merge/duplicate/capacity behavior now lives in
``band_sdk_core.ParticipantRoster`` and is covered at the
``ExecutionContext`` boundary (``tests/runtime/test_execution.py``), not here.
"""

from __future__ import annotations

from band.runtime.participants import participant_snapshot


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
