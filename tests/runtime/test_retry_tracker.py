"""Unit tests for MessageRetryTracker."""

from band.runtime.retry_tracker import MessageRetryTracker


class TestMessageRetryTracker:
    def test_first_attempt_returns_1(self):
        tracker = MessageRetryTracker(max_retries=3)
        attempts, exceeded = tracker.record_attempt("msg1")
        assert attempts == 1
        assert exceeded is False

    def test_tracks_multiple_attempts(self):
        tracker = MessageRetryTracker(max_retries=3)
        tracker.record_attempt("msg1")
        attempts, exceeded = tracker.record_attempt("msg1")
        assert attempts == 2
        assert exceeded is False

    def test_exceeds_max_retries(self):
        tracker = MessageRetryTracker(max_retries=2)
        tracker.record_attempt("msg1")  # 1
        tracker.record_attempt("msg1")  # 2
        attempts, exceeded = tracker.record_attempt("msg1")  # 3 > 2
        assert attempts == 3
        assert exceeded is True
        assert tracker.is_permanently_failed("msg1") is True

    def test_mark_success_clears_attempts(self):
        tracker = MessageRetryTracker(max_retries=3)
        tracker.record_attempt("msg1")
        tracker.record_attempt("msg1")
        tracker.mark_success("msg1")
        attempts, _ = tracker.record_attempt("msg1")
        assert attempts == 1  # Reset

    def test_mark_permanently_failed(self):
        tracker = MessageRetryTracker(max_retries=3)
        tracker.mark_permanently_failed("msg1")
        assert tracker.is_permanently_failed("msg1") is True

    def test_is_permanently_failed_false_initially(self):
        tracker = MessageRetryTracker(max_retries=3)
        assert tracker.is_permanently_failed("msg1") is False

    def test_different_messages_tracked_separately(self):
        tracker = MessageRetryTracker(max_retries=2)
        tracker.record_attempt("msg1")
        tracker.record_attempt("msg1")
        tracker.record_attempt("msg2")

        assert tracker.is_permanently_failed("msg1") is False
        assert tracker.is_permanently_failed("msg2") is False

    def test_max_retries_property(self):
        tracker = MessageRetryTracker(max_retries=5)
        assert tracker.max_retries == 5

    def test_room_id_in_constructor(self):
        tracker = MessageRetryTracker(max_retries=3, room_id="room-123")
        assert tracker._room_id == "room-123"

    def test_mark_success_on_unknown_message(self):
        """Should not raise on unknown message."""
        tracker = MessageRetryTracker(max_retries=3)
        tracker.mark_success("unknown")  # Should not raise

    def test_default_max_retries(self):
        tracker = MessageRetryTracker()
        assert tracker.max_retries == 1
