"""OneShotInvoker: parsing a message's inserted_at timestamp."""

from __future__ import annotations

from datetime import UTC, datetime

from band.runtime.oneshot import (
    _parse_inserted_at,
)


class TestParseInsertedAt:
    def test_parses_iso_z(self) -> None:
        dt = _parse_inserted_at("2026-05-21T10:00:00Z")
        assert dt.year == 2026 and dt.month == 5 and dt.day == 21
        assert dt.tzinfo is not None

    def test_parses_iso_offset(self) -> None:
        dt = _parse_inserted_at("2026-05-21T10:00:00+00:00")
        assert dt.tzinfo is not None

    def test_falls_back_to_now_on_invalid(self) -> None:
        before = datetime.now(UTC)
        dt = _parse_inserted_at("not a date")
        after = datetime.now(UTC)
        assert before <= dt <= after

    def test_falls_back_to_now_on_none(self) -> None:
        dt = _parse_inserted_at(None)
        assert dt.tzinfo is not None
