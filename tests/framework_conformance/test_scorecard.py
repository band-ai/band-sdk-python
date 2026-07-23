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

from types import SimpleNamespace

import pytest

from tests.e2e.baseline.agents import (
    PER_ADAPTER_MARKER,
    Adapter,
    ExcludedAdapter,
    ExpectedFailure,
    PerAdapter,
    per_adapter,
)
from tests.e2e.baseline.scorecard import (
    ScorecardCollector,
    ScorecardRow,
    merge,
    na_rows,
    outcome_row,
    to_markdown,
)


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


def test_per_adapter_marks_only_declared_adapter_as_xfail() -> None:
    @per_adapter(
        Adapter.ANTHROPIC,
        Adapter.CREWAI,
        xfail=[ExpectedFailure(Adapter.CREWAI, "known usage limitation")],
    )
    def fn() -> None: ...

    params = next(mark.args[1] for mark in fn.pytestmark if mark.name == "parametrize")
    marks_by_id = {param.id: {mark.name for mark in param.marks} for param in params}
    assert "xfail" not in marks_by_id[str(Adapter.ANTHROPIC)]
    assert "xfail" in marks_by_id[str(Adapter.CREWAI)]


def test_per_adapter_rejects_overlapping_exclusion_and_xfail() -> None:
    with pytest.raises(ValueError, match="both exclude and xfail"):
        per_adapter(
            exclude=[ExcludedAdapter(Adapter.CREWAI, "unsupported")],
            xfail=[ExpectedFailure(Adapter.CREWAI, "unsupported")],
        )


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
    nodeid: str, when: str, *, outcome: str, reason: str | None = None
) -> object:
    longrepr = ("m.py", 1, f"Skipped: {reason}") if reason is not None else None
    return SimpleNamespace(
        nodeid=nodeid,
        when=when,
        skipped=outcome == "skipped",
        failed=outcome == "failed",
        passed=outcome == "passed",
        longrepr=longrepr,
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


def test_outcome_row_setup_error_is_a_fail() -> None:
    _, row = outcome_row(_report("m.py::t[agno]", "setup", outcome="failed"))
    assert row.status == "fail"


def test_outcome_row_ignores_non_matrix_and_passing_setup() -> None:
    assert outcome_row(_report("p.py::test_no_param", "call", outcome="passed")) is None
    assert outcome_row(_report("m.py::t[agno]", "setup", outcome="passed")) is None
    assert outcome_row(_report("m.py::t[agno]", "teardown", outcome="passed")) is None


def test_outcome_row_ignores_non_adapter_parametrization() -> None:
    # A parametrized test whose param is not a registered adapter (an event type, not a
    # matrix cell) must not pollute the grid with a phantom "adapter".
    assert (
        outcome_row(_report("e.py::test_send_event[thought]", "call", outcome="passed"))
        is None
    )


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


def test_to_markdown_renders_grid_and_na_reasons() -> None:
    md = to_markdown(
        [
            ScorecardRow("m.py::test_x", "anthropic", "pass"),
            ScorecardRow("m.py::test_x", "crewai", "na", "no per-turn usage"),
        ]
    )
    assert "| test | anthropic | crewai |" in md
    assert "no per-turn usage" in md
