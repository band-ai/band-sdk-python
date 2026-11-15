"""Candidate builders for the selection contracts."""

from __future__ import annotations

from datetime import UTC, datetime

from band.runtime.selection import Candidate, Disposition, MessageKey
from tests.conftest import make_message_event


def key_at(second: int, message_id: str = "m") -> MessageKey:
    return MessageKey(datetime(2024, 1, 1, 0, 0, second, tzinfo=UTC), message_id)


def candidate(
    message_id: str,
    disposition: Disposition = Disposition.RUNNABLE,
    *,
    second: int,
    observed_metadata: dict[str, object] | None = None,
) -> Candidate:
    """A candidate whose key is ``second`` seconds into a fixed instant."""
    return Candidate(
        key_at(second, message_id),
        make_message_event(msg_id=message_id),
        disposition,
        observed_metadata,
    )
