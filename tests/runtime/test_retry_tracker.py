"""Compatibility facade tests for MessageRetryTracker (band_sdk_core.RetryTracker).

Capacity/overflow/bound mechanics live in band_sdk_core and are tested there;
these tests verify the SDK's public compatibility facade actually delegates to
the installed artifact and preserves its own log emission on top of it.
ExecutionContext constructs RetryTracker directly and never uses this facade
(see tests/runtime/test_execution.py for the delivery-path coverage).
"""

from __future__ import annotations

import logging

import pytest

from band.runtime.retry_tracker import MessageRetryTracker


class TestMessageRetryTrackerDelegation:
    def test_first_attempt_returns_1(self):
        tracker = MessageRetryTracker(max_retries=3)
        attempts, exceeded = tracker.record_attempt("msg1")
        assert (attempts, exceeded) == (1, False)

    def test_tracks_multiple_attempts(self):
        tracker = MessageRetryTracker(max_retries=3)
        tracker.record_attempt("msg1")
        attempts, exceeded = tracker.record_attempt("msg1")
        assert (attempts, exceeded) == (2, False)

    def test_exceeds_max_retries(self):
        tracker = MessageRetryTracker(max_retries=2)
        tracker.record_attempt("msg1")  # 1
        tracker.record_attempt("msg1")  # 2
        attempts, exceeded = tracker.record_attempt("msg1")  # 3 > 2
        assert (attempts, exceeded) == (3, True)
        assert tracker.is_permanently_failed("msg1") is True

    def test_mark_success_clears_attempts(self):
        tracker = MessageRetryTracker(max_retries=3)
        tracker.record_attempt("msg1")
        tracker.record_attempt("msg1")
        tracker.mark_success("msg1")
        attempts, _ = tracker.record_attempt("msg1")
        assert attempts == 1  # Reset

    def test_discard_attempt_uncharges_one_attempt(self):
        tracker = MessageRetryTracker(max_retries=3)
        tracker.record_attempt("msg1")
        tracker.record_attempt("msg1")
        tracker.discard_attempt("msg1")
        attempts, _ = tracker.record_attempt("msg1")
        assert attempts == 2

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

    def test_room_id_accepted_and_ignored(self):
        """room_id is compatibility-only: band_sdk_core.RetryTracker has no
        such concept, so construction must succeed and not affect behavior."""
        tracker = MessageRetryTracker(max_retries=3, room_id="room-123")
        assert tracker.max_retries == 3

    def test_mark_success_on_unknown_message(self):
        """Should not raise on unknown message."""
        tracker = MessageRetryTracker(max_retries=3)
        tracker.mark_success("unknown")  # Should not raise

    def test_default_max_retries(self):
        tracker = MessageRetryTracker()
        assert tracker.max_retries == 1


class TestMessageRetryTrackerLogging:
    """The facade's own log emission on top of the (silent) core delegate --
    band_sdk_core never logs, so these two lines are the facade's job alone."""

    def test_record_attempt_logs_error_when_exceeded(
        self, caplog: pytest.LogCaptureFixture
    ):
        tracker = MessageRetryTracker(max_retries=1)
        tracker.record_attempt("msg1")
        with caplog.at_level(logging.ERROR, logger="band.runtime.retry_tracker"):
            tracker.record_attempt("msg1")
        assert "exceeded max retries" in caplog.text
        assert all(record.levelno == logging.ERROR for record in caplog.records)

    def test_record_attempt_does_not_log_when_not_exceeded(
        self, caplog: pytest.LogCaptureFixture
    ):
        tracker = MessageRetryTracker(max_retries=3)
        with caplog.at_level(logging.ERROR, logger="band.runtime.retry_tracker"):
            tracker.record_attempt("msg1")
        assert caplog.records == []

    def test_mark_permanently_failed_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ):
        tracker = MessageRetryTracker(max_retries=3)
        with caplog.at_level(logging.WARNING, logger="band.runtime.retry_tracker"):
            tracker.mark_permanently_failed("msg1")
        assert "marked as permanently failed" in caplog.text
