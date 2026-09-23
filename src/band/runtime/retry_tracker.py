"""Public compatibility facade over band_sdk_core.RetryTracker."""

from __future__ import annotations

import logging

from band_sdk_core import RetryTracker

logger = logging.getLogger(__name__)


class MessageRetryTracker:
    """Tracks message processing attempts and permanent failures.

    A thin delegate over ``band_sdk_core.RetryTracker``, kept as a public
    compatibility symbol (``band.MessageRetryTracker`` /
    ``band.runtime.MessageRetryTracker``). ``ExecutionContext`` constructs
    ``RetryTracker`` directly instead of using this facade.
    """

    def __init__(self, max_retries: int = 1, room_id: str = "") -> None:
        # room_id is accepted for backward compatibility but unused: core's
        # RetryTracker has no concept of it (retry state is process-scoped,
        # keyed only by message id).
        self._tracker = RetryTracker(max_retries=max_retries)

    @property
    def max_retries(self) -> int:
        return self._tracker.max_retries

    def is_permanently_failed(self, msg_id: str) -> bool:
        """Check if message has exceeded max retries."""
        return self._tracker.is_permanently_failed(msg_id)

    def record_attempt(self, msg_id: str) -> tuple[int, bool]:
        """
        Record processing attempt.

        Returns:
            Tuple of (attempt_count, exceeded_max_retries)
        """
        attempts, exceeded = self._tracker.record_attempt(msg_id)
        if exceeded:
            logger.error(
                "Message %s exceeded max retries (%s), marking as permanently failed",
                msg_id,
                self._tracker.max_retries,
            )
        return attempts, exceeded

    def mark_success(self, msg_id: str) -> None:
        """Clear tracking for successfully processed message."""
        self._tracker.mark_success(msg_id)

    def discard_attempt(self, msg_id: str) -> None:
        """Uncharge the attempt aborted by our own control signal (interrupt/stop).

        A cycle cancelled by interrupt/stop never actually ran the handler, so
        it must not count against the message's retry budget. Decrements by
        one rather than clearing the counter: earlier genuine failures on the
        same message must stay charged, otherwise interrupting one retry
        silently resets the whole budget.
        """
        self._tracker.discard_attempt(msg_id)

    def mark_permanently_failed(self, msg_id: str) -> None:
        """Explicitly mark message as permanently failed."""
        self._tracker.mark_permanently_failed(msg_id)
        logger.warning("Message %s marked as permanently failed", msg_id)
