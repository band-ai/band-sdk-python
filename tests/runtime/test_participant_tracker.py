"""Unit tests for ParticipantTracker."""

from band.runtime.participant_tracker import ParticipantTracker


class TestParticipantTracker:
    def test_add_participant(self):
        tracker = ParticipantTracker()
        result = tracker.add({"id": "1", "name": "Alice", "type": "User"})
        assert result is True
        assert len(tracker.participants) == 1
        assert tracker.participants[0]["name"] == "Alice"

    def test_add_duplicate_returns_false(self):
        tracker = ParticipantTracker()
        tracker.add({"id": "1", "name": "Alice", "type": "User"})
        result = tracker.add({"id": "1", "name": "Alice", "type": "User"})
        assert result is False
        assert len(tracker.participants) == 1

    def test_remove_participant(self):
        tracker = ParticipantTracker()
        tracker.add({"id": "1", "name": "Alice", "type": "User"})
        result = tracker.remove("1")
        assert result is True
        assert len(tracker.participants) == 0

    def test_remove_nonexistent_returns_false(self):
        tracker = ParticipantTracker()
        result = tracker.remove("nonexistent")
        assert result is False

    def test_changed_true_on_first_call(self):
        tracker = ParticipantTracker()
        assert tracker.changed() is True

    def test_changed_false_after_mark_sent(self):
        tracker = ParticipantTracker()
        tracker.add({"id": "1", "name": "Alice", "type": "User"})
        tracker.mark_sent()
        assert tracker.changed() is False

    def test_changed_true_after_add(self):
        tracker = ParticipantTracker()
        tracker.mark_sent()
        tracker.add({"id": "1", "name": "Alice", "type": "User"})
        assert tracker.changed() is True

    def test_changed_true_after_remove(self):
        tracker = ParticipantTracker()
        tracker.add({"id": "1", "name": "Alice", "type": "User"})
        tracker.mark_sent()
        tracker.remove("1")
        assert tracker.changed() is True

    def test_set_loaded(self):
        tracker = ParticipantTracker()
        assert tracker.is_loaded is False
        tracker.set_loaded([{"id": "1", "name": "Alice", "type": "User"}])
        assert tracker.is_loaded is True
        assert len(tracker.participants) == 1

    def test_participants_returns_copy(self):
        tracker = ParticipantTracker()
        tracker.add({"id": "1", "name": "Alice", "type": "User"})
        participants = tracker.participants
        participants.clear()
        assert len(tracker.participants) == 1  # Original unchanged

    def test_add_normalizes_participant_fields(self):
        """Should only keep id, name, type fields."""
        tracker = ParticipantTracker()
        tracker.add(
            {
                "id": "1",
                "name": "Alice",
                "type": "User",
                "extra_field": "should_be_ignored",
            }
        )
        participant = tracker.participants[0]
        assert "extra_field" not in participant
        assert set(participant.keys()) == {"id", "name", "type", "handle"}

    def test_room_id_in_constructor(self):
        tracker = ParticipantTracker(room_id="room-123")
        assert tracker._room_id == "room-123"
