from __future__ import annotations

from datetime import UTC, datetime, timedelta

from band.adapters.letta import (
    LettaAdapter,
)


class TestFormatTimeAgo:
    def test_seconds(self) -> None:
        dt = datetime.now(UTC) - timedelta(seconds=30)
        assert "30s" == LettaAdapter._format_time_ago(dt)

    def test_minutes(self) -> None:
        dt = datetime.now(UTC) - timedelta(minutes=5)
        assert "5m" == LettaAdapter._format_time_ago(dt)

    def test_hours(self) -> None:
        dt = datetime.now(UTC) - timedelta(hours=3)
        assert "3h" == LettaAdapter._format_time_ago(dt)

    def test_one_hour(self) -> None:
        dt = datetime.now(UTC) - timedelta(hours=1)
        assert "1 hour" == LettaAdapter._format_time_ago(dt)

    def test_days(self) -> None:
        dt = datetime.now(UTC) - timedelta(days=5)
        assert "5d" == LettaAdapter._format_time_ago(dt)

    def test_one_day(self) -> None:
        dt = datetime.now(UTC) - timedelta(days=1)
        assert "1 day" == LettaAdapter._format_time_ago(dt)

    def test_naive_datetime_treated_as_utc(self) -> None:
        dt = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=10)
        result = LettaAdapter._format_time_ago(dt)
        assert "10m" == result
