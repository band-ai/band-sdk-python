"""A timestamp with no offset is read as UTC; an aware one is left alone."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from band.core.timestamps import assume_utc


class TestAssumeUtc:
    def test_a_naive_timestamp_compares_with_an_aware_one_as_utc(self) -> None:
        aware = datetime(2026, 5, 21, 10, 0, tzinfo=UTC)
        naive = aware.replace(tzinfo=None)

        assert assume_utc(naive) == aware

    def test_an_aware_timestamp_keeps_its_own_offset(self) -> None:
        plus_two = timezone(timedelta(hours=2))
        aware = datetime(2026, 5, 21, 10, 0, tzinfo=plus_two)

        assert assume_utc(aware).utcoffset() == timedelta(hours=2)
