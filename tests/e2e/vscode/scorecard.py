"""Scorecard emission for the VS Code Copilot surface.

Reuses the baseline scorecard's row schema and writer so the artifact merges
with the lane scorecards, but records rows itself: the baseline collector only
counts ``[adapter]``-parametrized matrix cells, and this suite's tests are
bespoke (unparametrized, one fixed surface column).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from tests.e2e.baseline.scorecard import ScorecardRow, Status, write_json

logger = logging.getLogger(__name__)

# The scorecard column for this surface (a surface id, not a registered
# matrix adapter — deliberately absent from the baseline Adapter registry).
SURFACE_ID = "copilot_vscode"

# L4 usage has no test function: the surface offers nothing to assert — Copilot
# Chat exposes no per-turn usage/billing signal to the harness and posts no
# Band-side usage events. The rationale ships as a scorecard row instead.
USAGE_NA_ROW = ScorecardRow(
    test="tests/e2e/vscode/test_copilot_chat.py::usage_accounting",
    adapter=SURFACE_ID,
    status="na",
    reason=(
        "Copilot Chat in VS Code exposes no per-turn usage/billing signal to "
        "the harness and emits no Band-side usage events"
    ),
)


def outcome_status(report: pytest.TestReport) -> Status | None:
    """The row status one setup/call report contributes (same semantics as the
    baseline collector: skip anywhere = skip, failed setup or call = fail,
    passing call = pass, passing setup = no verdict yet)."""
    if report.when not in ("setup", "call"):
        return None
    if report.skipped:
        return "skip"
    if report.failed:
        return "fail"
    if report.when == "call":
        return "pass"
    return None


class VSCodeScorecard:
    """Pytest plugin: one row per suite test plus the fixed L4 ``na`` row,
    written with a ``.meta.json`` environment sidecar at session end."""

    def __init__(self, path: str | Path, suite_dir: Path) -> None:
        self._path = Path(path)
        self._suite_dir = suite_dir
        self._rows: dict[str, ScorecardRow] = {}
        self.metadata: dict[str, str] = {}

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if not Path(report.fspath).resolve().is_relative_to(self._suite_dir):
            return
        status = outcome_status(report)
        if status is None:
            return
        self._rows[report.nodeid] = ScorecardRow(
            test=report.nodeid, adapter=SURFACE_ID, status=status
        )

    def scorecard(self) -> list[ScorecardRow]:
        rows = dict(self._rows)
        rows[USAGE_NA_ROW.test] = USAGE_NA_ROW
        return sorted(rows.values(), key=lambda row: row.test)

    def pytest_sessionfinish(self) -> None:
        write_json(self.scorecard(), self._path)
        meta_path = self._path.with_suffix(self._path.suffix + ".meta.json")
        meta_path.write_text(json.dumps(self.metadata, indent=2) + "\n")
        logger.info("scorecard written to %s (+ %s)", self._path, meta_path.name)
