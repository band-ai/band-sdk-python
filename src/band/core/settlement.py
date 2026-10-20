"""Result vocabulary for terminal message writes."""

from __future__ import annotations

from enum import StrEnum


class SettlementResult(StrEnum):
    """What a terminal (processed/failed) write established about the platform."""

    ACCEPTED = "accepted"
    """The platform confirmed the write; the message is settled."""

    UNCONFIRMED = "unconfirmed"
    """The outcome is unknown (transport failure, unexpected status or body); the
    same write is safe to retry."""

    NOT_FOUND = "not_found"
    """The platform answered 404 with its ``not_found`` error: the message is
    absent, so retrying cannot succeed."""

    REJECTED = "rejected"
    """The platform answered 422 naming the missing processing attempt: there is
    nothing to settle, so retrying cannot succeed."""
