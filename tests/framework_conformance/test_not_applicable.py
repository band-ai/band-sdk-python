"""A selection gives every other live adapter an N/A row with its reason."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

import tests.e2e.baseline.toolkit.adapters as adapters_module
from tests.e2e.baseline.agents import ExcludedAdapter, not_applicable
from tests.e2e.baseline.toolkit.adapters import Adapter, spec_for, specs

REASON = "no harness for this signal"
SELECTED = (Adapter.CLAUDE_SDK, Adapter.CODEX)


class TestNotApplicable:
    def test_selection_and_rows_partition_the_live_registry(self) -> None:
        rows = not_applicable(SELECTED, REASON)
        assert {row.reason for row in rows} == {REASON}
        assert sorted([*SELECTED, *(row.adapter for row in rows)]) == sorted(
            spec.id for spec in specs()
        )

    def test_rows_follow_the_registry_rather_than_a_fixed_list(self) -> None:
        row = ExcludedAdapter(Adapter.LANGGRAPH, REASON)
        pending = replace(
            spec_for(Adapter.LANGGRAPH), e2e_pending="synthetic: backend not CI-wired"
        )
        with patch.dict(adapters_module._REGISTRY, {Adapter.LANGGRAPH: pending}):
            assert row not in not_applicable(SELECTED, REASON)
        assert row in not_applicable(SELECTED, REASON)
