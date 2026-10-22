"""Program operator inputs respect the service's UTF-8 wire budgets."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from band.contracts.models import JournalQuery
from band.contracts.programs import ProgramControl


class TestLimits:
    @pytest.mark.parametrize(
        "reason",
        ["x" * 2000, "é" * 1000, "🦊" * 500],
        ids=["ascii", "two-byte", "four-byte"],
    )
    def test_control_accepts_exact_byte_boundary_and_snapshots_trimmed_reason(
        self, reason: str
    ) -> None:
        request = ProgramControl(
            id="control", action="wake", reason=" \t" + reason + "\n "
        )
        assert request.reason == reason
        assert request.body()["reason"] == reason
        assert json.loads(request.encoded)["reason"] == reason

    @pytest.mark.parametrize(
        "reason",
        ["x" * 2001, "é" * 1000 + "x", "🦊" * 500 + "x"],
        ids=["ascii", "two-byte", "four-byte"],
    )
    def test_control_rejects_a_reason_above_the_byte_budget(self, reason: str) -> None:
        with pytest.raises(ValidationError):
            ProgramControl(id="control", action="wake", reason=reason)

    @pytest.mark.parametrize(
        "reason",
        ["", " ", "\t\n", "\u2003"],
        ids=["empty", "space", "ascii-whitespace", "em-space"],
    )
    def test_control_rejects_whitespace_only_reasons(self, reason: str) -> None:
        with pytest.raises(ValidationError):
            ProgramControl(id="control", action="wake", reason=reason)

    def test_stop_keeps_its_optional_reason(self) -> None:
        assert ProgramControl(id="control", action="stop").body() == {
            "id": "control",
            "action": "stop",
        }

    @pytest.mark.parametrize(
        "query",
        ["x" * 256, "é" * 128, "🦊" * 64, " " + "é" * 127 + " "],
        ids=["ascii", "two-byte", "four-byte", "raw-whitespace"],
    )
    def test_journal_query_preserves_raw_text_at_the_byte_boundary(
        self, query: str
    ) -> None:
        request = JournalQuery(query=query)
        assert request.query == query
        assert request.body()["query"] == query
        assert json.loads(request.encoded)["query"] == query

    @pytest.mark.parametrize(
        "query",
        ["x" * 257, "é" * 128 + "x", "🦊" * 64 + "x"],
        ids=["ascii", "two-byte", "four-byte"],
    )
    def test_journal_query_rejects_text_above_the_byte_budget(self, query: str) -> None:
        with pytest.raises(ValidationError):
            JournalQuery(query=query)
