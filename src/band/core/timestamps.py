"""Timestamp normalization shared across layers."""

from __future__ import annotations

from datetime import UTC, datetime


def assume_utc(moment: datetime) -> datetime:
    """Return ``moment`` as timezone-aware, reading a naive one as UTC.

    The platform emits UTC timestamps, but a parsed one can arrive without an
    offset; comparing it with an aware one would raise ``TypeError``. An aware
    moment keeps its own offset.
    """
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
