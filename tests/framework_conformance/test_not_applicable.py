"""A selection gives every other live adapter an N/A row with its reason."""

from __future__ import annotations

from tests.e2e.baseline.agents import not_applicable
from tests.e2e.baseline.toolkit.adapters import Adapter, specs

REASON = "no harness for this signal"
SELECTED = (Adapter.CLAUDE_SDK, Adapter.CODEX)


class TestNotApplicable:
    def test_selection_and_rows_partition_the_live_registry(self) -> None:
        rows = not_applicable(SELECTED, REASON)
        assert {row.reason for row in rows} == {REASON}
        assert sorted([*SELECTED, *(row.adapter for row in rows)]) == sorted(
            spec.id for spec in specs()
        )
