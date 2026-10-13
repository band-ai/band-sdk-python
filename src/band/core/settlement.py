"""What a terminal (processed/failed) write established about the platform."""

from __future__ import annotations

from enum import StrEnum


class SettlementResult(StrEnum):
    """What a terminal (processed/failed) write established about the platform."""

    ACCEPTED = "accepted"
    UNCONFIRMED = "unconfirmed"
    NOT_FOUND = "not_found"
    REJECTED = "rejected"
