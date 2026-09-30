"""Unit guards for the adapter×test scorecard (``ExcludedAdapter`` + ``scorecard``).

Pure-function tests (no live platform), so they run in the ordinary unit suite on every
PR rather than only in the manually-triggered E2E job — the scorecard is what keeps an
excluded adapter from vanishing from the matrix, and its reasons feed CI gating, so a
regression here silently drops rows or loses the N/A explanations.

Covered:
* ``ExcludedAdapter`` requires a non-empty reason at construction (so ``@per_adapter``
  cannot exclude an adapter without saying why);
* ``@per_adapter`` drops the excluded adapters from the fanned cells yet carries their
  reasons on the ``PerAdapter`` marker, and rejects an unregistered exclusion;
* ``na_rows`` turns those marker records into N/A rows; ``outcome_row`` maps a test
  report to pass / fail / skip; ``merge`` unions per-lane cards (a real outcome beats a
  skip, an N/A is never clobbered).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.e2e.baseline.agents import (
    LANE_MARKER,
    PER_ADAPTER_MARKER,
    Adapter,
    ExcludedAdapter,
    PerAdapter,
    per_adapter,
)
from tests.e2e.baseline.scorecard import (
    ENV_GATED_MARKER,
    SUITE_ADAPTER,
    ExpectedCell,
    GateResult,
    ScorecardCollector,
    ScorecardRow,
    Status,
    digest_body,
    env_gated_skip,
    expected_cells,
    gate_manifest,
    gate_summary,
    merge,
    na_rows,
    outcome_row,
    to_markdown,
)
from tests.e2e.baseline.toolkit.ci_lanes import ci_lanes
from tests.paths import REPO_ROOT

# --- ExcludedAdapter: a reason is mandatory -----------------------------------------


def test_excluded_adapter_keeps_adapter_and_reason() -> None:
    excluded = ExcludedAdapter(Adapter.CREWAI, "no per-turn usage")
    assert excluded.adapter is Adapter.CREWAI
    assert excluded.reason == "no per-turn usage"


@pytest.mark.parametrize("reason", ["", "   ", "\n\t"])
def test_excluded_adapter_rejects_empty_reason(reason: str) -> None:
    with pytest.raises(ValueError, match="non-empty reason"):
        ExcludedAdapter(Adapter.CREWAI, reason)


# --- @per_adapter: excluded cells drop out, but their reasons ride the marker --------


def _per_adapter_marker(fn: object) -> PerAdapter:
    """The ``PerAdapter`` payload a decorated function carries."""
    for mark in fn.pytestmark:  # type: ignore[attr-defined]
        if mark.name == PER_ADAPTER_MARKER:
            return mark.args[0]
    raise AssertionError("no per_adapter marker on the decorated function")


def _parametrized_adapter_ids(fn: object) -> list[str]:
    for mark in fn.pytestmark:  # type: ignore[attr-defined]
        if mark.name == "parametrize":
            return [param.id for param in mark.args[1]]
    raise AssertionError("no parametrize marker on the decorated function")


def test_per_adapter_excludes_cell_but_carries_reason() -> None:
    @per_adapter(exclude=[ExcludedAdapter(Adapter.CREWAI, "cumulative usage")])
    def fn() -> None: ...

    assert str(Adapter.CREWAI) not in _parametrized_adapter_ids(fn)
    payload = _per_adapter_marker(fn)
    assert payload.exclude == (ExcludedAdapter(Adapter.CREWAI, "cumulative usage"),)


def test_per_adapter_rejects_unregistered_exclusion() -> None:
    # Adapter is a StrEnum, so a plain unregistered string is an unknown id.
    with pytest.raises(ValueError, match="unregistered adapters"):
        per_adapter(exclude=[ExcludedAdapter("no-such-adapter", "typo")])(lambda: None)  # type: ignore[arg-type]


# --- na_rows: marker exclusions become N/A rows -------------------------------------


class _FakeItem:
    """A ``pytest.Item`` stand-in exposing only what ``na_rows`` reads."""

    def __init__(self, nodeid: str, exclude: tuple[ExcludedAdapter, ...] = ()) -> None:
        self.nodeid = nodeid
        build = PerAdapter(prompt=None, features=None, tools=None, exclude=exclude)
        self._marker = SimpleNamespace(args=(build,))

    def get_closest_marker(self, name: str) -> object | None:
        return self._marker if name == PER_ADAPTER_MARKER else None


def test_na_rows_from_marker_exclusions() -> None:
    exclude = (
        ExcludedAdapter(Adapter.CREWAI, "cumulative usage"),
        ExcludedAdapter(Adapter.CREWAI_FLOW, "flow internals"),
    )
    # Two collected cells of the same test share the marker; the reasons dedupe by key.
    items = [
        _FakeItem("m.py::test_x[anthropic]", exclude),
        _FakeItem("m.py::test_x[agno]", exclude),
    ]
    rows = na_rows(items)
    assert rows[("m.py::test_x", "crewai")] == ScorecardRow(
        "m.py::test_x", "crewai", "na", "cumulative usage"
    )
    assert rows[("m.py::test_x", "crewai_flow")].reason == "flow internals"
    assert len(rows) == 2


def test_na_rows_ignores_items_without_per_adapter_marker() -> None:
    class _Bare:
        nodeid = "p.py::test_provisioning"

        def get_closest_marker(self, name: str) -> None:
            return None

    assert na_rows([_Bare()]) == {}


# --- outcome_row: a test report -> a cell verdict -----------------------------------


def _report(
    nodeid: str,
    when: str,
    *,
    outcome: str,
    reason: str | None = None,
    keywords: tuple[str, ...] = (),
) -> object:
    longrepr = ("m.py", 1, f"Skipped: {reason}") if reason is not None else None
    return SimpleNamespace(
        nodeid=nodeid,
        when=when,
        skipped=outcome == "skipped",
        failed=outcome == "failed",
        passed=outcome == "passed",
        longrepr=longrepr,
        keywords=frozenset(keywords),
    )


def test_outcome_row_call_pass_and_fail() -> None:
    key, row = outcome_row(_report("m.py::t[anthropic]", "call", outcome="passed"))
    assert (key, row.status) == (("m.py::t", "anthropic"), "pass")
    _, fail = outcome_row(_report("m.py::t[crewai]", "call", outcome="failed"))
    assert fail.status == "fail"


def test_outcome_row_setup_skip_captures_reason() -> None:
    _, row = outcome_row(
        _report("m.py::t[agno]", "setup", outcome="skipped", reason="lane 'core'")
    )
    assert (row.status, row.reason) == ("skip", "lane 'core'")


def test_env_gated_skip_applies_both_skipif_and_marker() -> None:
    @env_gated_skip(True, reason="flag is off")
    def fn() -> None: ...

    marks = {mark.name for mark in fn.pytestmark}  # type: ignore[attr-defined]
    assert marks == {"skipif", ENV_GATED_MARKER}


def test_outcome_row_env_gated_skip_reports_na_not_missing() -> None:
    # A deployment flag that is permanently off in this environment (e.g. the SaaS
    # E2E lanes' ff_file_transfer) must never read as "missing coverage" -- see
    # ENV_GATED_MARKER.
    _, row = outcome_row(
        _report(
            "m.py::t[anthropic]",
            "setup",
            outcome="skipped",
            reason="E2E_FILE_TRANSFER is not true",
            keywords=(ENV_GATED_MARKER,),
        )
    )
    assert (row.status, row.reason) == ("na", "E2E_FILE_TRANSFER is not true")


def test_outcome_row_setup_error_is_a_fail() -> None:
    _, row = outcome_row(_report("m.py::t[agno]", "setup", outcome="failed"))
    assert row.status == "fail"


def test_outcome_row_records_bespoke_and_ignores_passing_setup() -> None:
    key, row = outcome_row(_report("p.py::test_no_param", "call", outcome="passed"))
    assert key == ("p.py::test_no_param", SUITE_ADAPTER)
    assert row.status == "pass"
    assert outcome_row(_report("m.py::t[agno]", "setup", outcome="passed")) is None
    assert outcome_row(_report("m.py::t[agno]", "teardown", outcome="passed")) is None


def test_outcome_row_keeps_non_adapter_parametrization_distinct() -> None:
    key, row = outcome_row(
        _report("e.py::test_send_event[thought]", "call", outcome="passed")
    )
    assert key == ("e.py::test_send_event[thought]", SUITE_ADAPTER)
    assert row.status == "pass"


# --- collector + merge: a run's rows, then the cross-lane union ----------------------


def test_collector_combines_outcomes_and_na() -> None:
    collector = ScorecardCollector(path="unused")
    collector.pytest_runtest_logreport(
        _report("m.py::t[anthropic]", "call", outcome="passed")
    )
    item = _FakeItem(
        "m.py::t[anthropic]", (ExcludedAdapter(Adapter.CREWAI, "no usage"),)
    )
    rows = {(r.test, r.adapter): r for r in collector.scorecard([item])}
    assert rows[("m.py::t", "anthropic")].status == "pass"
    assert rows[("m.py::t", "crewai")].status == "na"


def test_collector_keeps_a_failed_first_attempt() -> None:
    collector = ScorecardCollector(path="unused")
    nodeid = "m.py::t[anthropic]"
    collector.pytest_runtest_logreport(_report(nodeid, "call", outcome="failed"))
    collector.pytest_runtest_logreport(_report(nodeid, "call", outcome="passed"))
    rows = collector.scorecard([_FakeItem(nodeid)])
    assert rows == [ScorecardRow("m.py::t", "anthropic", "fail")]


def test_merge_prefers_real_outcome_over_skip_and_keeps_na() -> None:
    lane_a = [
        ScorecardRow("t", "anthropic", "pass"),
        ScorecardRow("t", "crewai", "skip", "lane"),
        ScorecardRow("t", "crewai_flow", "na", "no usage"),
    ]
    lane_b = [
        ScorecardRow("t", "anthropic", "skip", "lane"),
        ScorecardRow("t", "crewai", "fail"),
        ScorecardRow("t", "crewai_flow", "na", "no usage"),
    ]
    merged = {(r.test, r.adapter): r for r in merge([lane_a, lane_b])}
    assert merged[("t", "anthropic")].status == "pass"
    assert merged[("t", "crewai")].status == "fail"
    assert merged[("t", "crewai_flow")].status == "na"


def test_merge_leaves_a_never_run_cell_visible_as_skip() -> None:
    merged = merge([[ScorecardRow("t", "letta", "skip", "lane")]])
    assert merged[0].status == "skip"


# --- gate: required lane x OS cells ---------------------------------------------------

# Two distinct lanes with at least one adapter each, read off the real registry rather
# than hand-picked ids — a gate case only needs "two different homes", not which ones.
_LANE_A, _LANE_B = [lane for lane in ci_lanes() if lane.adapters][:2]
_ADAPTER_A = str(_LANE_A.adapters[0])
_ADAPTER_B = str(_LANE_B.adapters[0])


def test_gate_fails_on_a_fail_cell() -> None:
    row = ScorecardRow("t", _ADAPTER_A, "fail")
    lane = str(_LANE_A.id)
    result = gate_manifest(
        {(lane, "ubuntu"): [row]},
        [ExpectedCell("t", _ADAPTER_A, (lane,), "pass")],
        {lane: ("ubuntu",)},
        lane,
        "ubuntu",
    )
    assert result.ok is False
    assert result.failing == (
        ScorecardRow("t", _ADAPTER_A, "fail", lane=lane, os="ubuntu"),
    )
    assert result.missing == ()


def test_gate_ignores_a_skip_cell_whose_lane_is_out_of_scope() -> None:
    lane = str(_LANE_A.id)
    row = ScorecardRow("t", _ADAPTER_B, "skip", "other lane")
    result = gate_manifest(
        {(lane, "ubuntu"): [row]},
        [ExpectedCell("t", _ADAPTER_B, (str(_LANE_B.id),), "pass")],
        {lane: ("ubuntu",), str(_LANE_B.id): ("ubuntu",)},
        lane,
        "ubuntu",
    )
    assert result.ok is True


def test_gate_fails_a_skip_cell_whose_lane_was_expected_to_run() -> None:
    lane = str(_LANE_A.id)
    row = ScorecardRow("t", _ADAPTER_A, "skip", "missing key")
    result = gate_manifest(
        {(lane, "ubuntu"): [row]},
        [ExpectedCell("t", _ADAPTER_A, (lane,), "pass")],
        {lane: ("ubuntu",)},
        lane,
        "ubuntu",
    )
    assert result.ok is False
    assert result.missing == (
        ScorecardRow("t", _ADAPTER_A, "skip", "missing key", lane=lane, os="ubuntu"),
    )


@pytest.mark.parametrize(("status", "ok"), [("pass", True), ("fail", False)])
def test_a_selector_run_gates_only_the_cells_it_reported(
    status: Status, ok: bool
) -> None:
    lane = str(_LANE_A.id)
    selected = ExpectedCell("t_selected", _ADAPTER_A, (lane,), "pass")
    deselected = ExpectedCell("t_deselected", _ADAPTER_A, (lane,), "pass")
    result = gate_manifest(
        {(lane, "ubuntu"): [ScorecardRow(selected.test, selected.adapter, status)]},
        [selected, deselected],
        {lane: ("ubuntu",)},
        lane,
        "ubuntu",
        selected_tests_only=True,
    )
    assert result.ok is ok
    assert result.missing == ()


def test_manifest_gate_requires_bespoke_results_on_each_os() -> None:
    cell = ExpectedCell("smoke.py::test_control", SUITE_ADAPTER, ("core",), "pass")
    pass_row = ScorecardRow(cell.test, cell.adapter, "pass")
    result = gate_manifest(
        {("core", "ubuntu"): [pass_row]},
        [cell],
        {"core": ("ubuntu", "windows")},
        "core",
        "all",
    )
    assert result.ok is False
    assert [(row.lane, row.os, row.test) for row in result.missing] == [
        ("core", "windows", cell.test)
    ]


def test_manifest_gate_rejects_skip_and_respects_explicit_na() -> None:
    required = ExpectedCell("smoke.py::test_live", "anthropic", ("core",), "pass")
    unsupported = ExpectedCell("smoke.py::test_file", "anthropic", ("core",), "na")
    result = gate_manifest(
        {
            ("core", "ubuntu"): [
                ScorecardRow(required.test, required.adapter, "skip", "missing key"),
                ScorecardRow(unsupported.test, unsupported.adapter, "na", "flag off"),
            ]
        },
        [required, unsupported],
        {"core": ("ubuntu",)},
        "core",
        "ubuntu",
    )
    assert result.ok is False
    assert [row.test for row in result.missing] == [required.test]


def test_expected_manifest_matches_collected_baseline(tmp_path: Path) -> None:
    generated = tmp_path / "expected-cells.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.e2e.baseline.scorecard",
            "manifest",
            "--out",
            str(generated),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    checked_in = REPO_ROOT / "tests/e2e/baseline/expected-cells.json"
    fresh = json.loads(generated.read_text())
    committed = json.loads(checked_in.read_text())
    assert fresh["supported_os"] == committed["supported_os"]
    current = {(cell["test"], cell["adapter"]): cell for cell in fresh["cells"]}
    expected = {(cell["test"], cell["adapter"]): cell for cell in committed["cells"]}
    assert all(expected.get(key) == cell for key, cell in current.items())
    extra = [cell for key, cell in expected.items() if key not in current]
    assert extra and all(
        cell["test"].startswith("tests/e2e/baseline/smoke/adapters/test_parlant.py::")
        for cell in extra
    )


class FakeMatrixItem:
    """A ``pytest.Item`` stand-in exposing what ``lane_selection.expected_lane`` reads:
    its ``adapter_id`` callspec param and an optional ``@lane`` override marker."""

    def __init__(
        self, nodeid: str, adapter: str, override_lane: str | None = None
    ) -> None:
        self.nodeid = nodeid
        self.callspec = SimpleNamespace(params={"adapter_id": adapter})
        self._override = override_lane

    def get_closest_marker(self, name: str) -> object | None:
        if name == LANE_MARKER and self._override is not None:
            return SimpleNamespace(args=(self._override,))
        return None


def test_gate_honors_a_lane_pin_over_the_adapters_home_lane() -> None:
    """A cell pinned by ``@lane`` cross-lane must gate on the pin, not the home lane.

    Reproduces the scenario ``ADDING_AN_ADAPTER.md`` prescribes: a ``@per_adapter``
    cell whose adapter's home lane is ``_LANE_A`` but is pinned to ``_LANE_B`` (e.g. to
    share an extra with a cross-lane peer). Using the home lane alone here would
    report this cell "missing" the moment its home lane's job runs — a legitimate
    lane-scoped skip, not a silent failure.
    """
    item = FakeMatrixItem(f"m.py::t[{_ADAPTER_A}]", _ADAPTER_A, str(_LANE_B.id))
    cell = next(c for c in expected_cells([item]) if c.adapter == _ADAPTER_A)
    assert cell.lanes == (str(_LANE_B.id),)

    # The adapter's home lane (_LANE_A) ran this invocation; the cell is pinned
    # elsewhere, so it must NOT be reported missing here.
    home_only = gate_manifest(
        {(str(_LANE_A.id), "ubuntu"): []},
        [cell],
        {str(_LANE_A.id): ("ubuntu",), str(_LANE_B.id): ("ubuntu",)},
        str(_LANE_A.id),
        "ubuntu",
    )
    assert home_only.ok is True
    assert home_only.missing == ()

    # The pinned lane (_LANE_B) ran and reported nothing for this cell — that is a
    # real gap.
    pinned_ran = gate_manifest(
        {(str(_LANE_B.id), "ubuntu"): []},
        [cell],
        {str(_LANE_A.id): ("ubuntu",), str(_LANE_B.id): ("ubuntu",)},
        str(_LANE_B.id),
        "ubuntu",
    )
    assert pinned_ran.ok is False
    assert pinned_ran.missing[0].test == cell.test


def test_environment_gated_na_is_expected_in_every_lane() -> None:
    class FileCell(FakeMatrixItem):
        def get_closest_marker(self, name: str) -> object | None:
            if name == ENV_GATED_MARKER:
                return SimpleNamespace()
            if name == "skipif":
                return SimpleNamespace(kwargs={"reason": "file transfer unavailable"})
            return super().get_closest_marker(name)

    item = FileCell(f"m.py::t[{_ADAPTER_A}]", _ADAPTER_A)
    cell = next(c for c in expected_cells([item]) if c.adapter == _ADAPTER_A)
    assert cell.status == "na"
    assert str(_LANE_B.id) in cell.lanes
    result = gate_manifest(
        {
            (str(_LANE_B.id), "ubuntu"): [
                ScorecardRow(cell.test, cell.adapter, "na", cell.reason)
            ]
        },
        [cell],
        {str(_LANE_B.id): ("ubuntu",)},
        str(_LANE_B.id),
        "ubuntu",
    )
    assert result.ok is True


def test_gate_summary_reports_totals_and_names_the_culprit() -> None:
    rows = [
        ScorecardRow("t", _ADAPTER_A, "pass"),
        ScorecardRow("t", _ADAPTER_B, "fail"),
    ]
    result = GateResult(False, (rows[1],), ())
    summary = gate_summary(result, rows)
    assert "GATE: FAIL" in summary
    assert _ADAPTER_B in summary


# --- digest_body: the email-safe half of gate_summary (no header, no grid) ----------


def test_digest_body_all_clear_has_no_problem_sections() -> None:
    rows = [ScorecardRow("t", _ADAPTER_A, "pass"), ScorecardRow("t", _ADAPTER_B, "na")]
    result = GateResult(True, (), ())
    body = digest_body(result, rows)
    assert "| Passed | Failed | N/A | Skipped |" in body
    assert "| 1 | 0 | 1 | 0 |" in body
    assert "Failing" not in body
    assert "Missing" not in body


def test_digest_body_lists_failing_and_missing_separately() -> None:
    failing = ScorecardRow("t", _ADAPTER_A, "fail")
    missing = ScorecardRow("t", _ADAPTER_B, "skip", "lane 'core'")
    result = GateResult(False, (failing,), (missing,))
    body = digest_body(result, [failing, missing])
    assert "**Failing**" in body
    assert f"`{_ADAPTER_A}`" in body.split("**Missing**")[0]
    assert "**Missing** (lane ran, no result)" in body
    assert f"`{_ADAPTER_B}`" in body.split("**Missing**")[1]


def test_digest_body_never_renders_a_grid() -> None:
    rows = [ScorecardRow("t", _ADAPTER_A, "fail")]
    result = GateResult(False, (rows[0],), ())
    body = digest_body(result, rows)
    assert "| test |" not in body


def test_to_markdown_renders_grid_and_na_reasons() -> None:
    md = to_markdown(
        [
            ScorecardRow("m.py::test_x", "anthropic", "pass"),
            ScorecardRow("m.py::test_x", "crewai", "na", "no per-turn usage"),
        ]
    )
    assert "| test | anthropic | crewai |" in md
    assert "no per-turn usage" in md
