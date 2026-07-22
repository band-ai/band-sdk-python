"""Message retry tracking. Sync, unit-testable."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class MessageRetryTracker:
    """
    Tracks message processing attempts and permanent failures.

    Used by ExecutionContext to:
    - Prevent infinite retry loops
    - Skip permanently failed messages
    """

    def __init__(self, max_retries: int = 1, room_id: str = ""):
        self._max_retries = max_retries
        self._room_id = room_id
        self._attempts: dict[str, int] = {}
        self._failed: set[str] = set()

    @property
    def max_retries(self) -> int:
        return self._max_retries

    def is_permanently_failed(self, msg_id: str) -> bool:
        """Check if message has exceeded max retries."""
        return msg_id in self._failed

    def record_attempt(self, msg_id: str) -> tuple[int, bool]:
        """
        Record processing attempt.

        Returns:
            Tuple of (attempt_count, exceeded_max_retries)
        """
        attempts = self._attempts.get(msg_id, 0) + 1
        self._attempts[msg_id] = attempts

        exceeded = attempts > self._max_retries
        if exceeded:
            self._failed.add(msg_id)
            logger.error(
                "Message %s exceeded max retries (%s), marking as permanently failed",
                msg_id,
                self._max_retries,
            )

        return attempts, exceeded

    def mark_success(self, msg_id: str) -> None:
        """Clear tracking for successfully processed message."""
        self._attempts.pop(msg_id, None)

    def discard_attempt(self, msg_id: str) -> None:
        """Uncharge the attempt aborted by our own control signal (interrupt/stop).

        A cycle cancelled by interrupt/stop never actually ran the handler, so
        it must not count against the message's retry budget. Decrement by one
        rather than clearing the counter: earlier genuine failures on the same
        message must stay charged, otherwise interrupting one retry silently
        resets the whole budget.
        """
        remaining = self._attempts.get(msg_id, 0) - 1
        if remaining > 0:
            self._attempts[msg_id] = remaining
        else:
            self._attempts.pop(msg_id, None)

    def mark_permanently_failed(self, msg_id: str) -> None:
        """Explicitly mark message as permanently failed."""
        self._failed.add(msg_id)
        logger.warning("Message %s marked as permanently failed", msg_id)
