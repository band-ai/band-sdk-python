"""Result vocabulary for message delivery-status writes."""

from __future__ import annotations

from enum import StrEnum


class SettlementResult(StrEnum):
    """What a delivery-status write established about the platform."""

    ACCEPTED = "accepted"
    """The platform confirmed the write."""

    UNCONFIRMED = "unconfirmed"
    """The outcome is unknown (transport failure, unexpected status or body); the
    same write is safe to retry."""

    NOT_FOUND = "not_found"
    """The platform answered 404 with its ``not_found`` error: the message is
    absent, so retrying cannot succeed."""

    REJECTED = "rejected"
    """The platform answered 422 naming the missing processing attempt: there is
    nothing to settle, so retrying cannot succeed."""
