"""First-attempt baseline scorecards and the expected lane/OS cell manifest.

Excluded adapters produce no test node (``specs()`` omits them), so a matrix cell an
adapter opts out of would otherwise vanish from the results with its reason buried in a
code comment. This module makes the full grid observable:

* :func:`na_rows` reads each ``@per_adapter`` marker's ``exclude`` records (the reasons
  live on the marker — see ``agents.PerAdapter``) and emits an ``N/A`` row per excluded
  cell, so no cell disappears without a trace.
* :class:`ScorecardCollector` records the run outcome (pass / fail / skip) of every
  collected cell from its test report — exact ``nodeid`` keys, no junit-name scraping.
* :func:`merge` unions the per-lane scorecards CI emits (each lane runs only its own
  cells; the rest are ``skip``) into one grid.
* :func:`gate_manifest` requires each checked-in cell in its assigned lane on
  every supported selected OS, including bespoke smokes.

The pieces are pure functions so they unit-test without a live platform; the conftest is
a thin hook delegate, and ``python -m tests.e2e.baseline.scorecard merge`` is the
post-run CI step that folds the lanes together and gates on the result.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal, TypeVar

import pytest

from tests.e2e.baseline.agents import PER_ADAPTER_MARKER, Adapter, PerAdapter
from tests.e2e.baseline.lane_selection import expected_lane as _resolve_expected_lane
from tests.e2e.baseline.toolkit.ci_lanes import (
    adapter_home_lanes,
    ci_lanes,
    known_lane_ids,
)

logger = logging.getLogger(__name__)

# Only a registered adapter id names a matrix cell. Other parametrized tests carry
# unrelated params in their nodeid (e.g. ``test_send_event[thought]``), which must not
# be mistaken for adapters when reading outcomes off a report.
_ADAPTER_IDS: frozenset[str] = frozenset(str(adapter) for adapter in Adapter)
SUITE_ADAPTER = "suite"

# ``na`` = deliberately excluded (with a reason); ``skip`` = collected but not run in this
# lane (lane scoping / E2E disabled). Ranked so a real outcome beats ``skip`` when the
# per-lane scorecards are unioned, and an ``N/A`` is never overwritten by a ``skip``.
Status = Literal["pass", "fail", "skip", "na"]
_RANK: dict[Status, int] = {"skip": 0, "na": 1, "pass": 2, "fail": 3}

# Distinguishes unsupported deployment capabilities from a required cell that skipped.
ENV_GATED_MARKER = "env_gated_skip"

_F = TypeVar("_F", bound=Callable[..., object])


def env_gated_skip(condition: bool, reason: str) -> Callable[[_F], _F]:
    """``skipif``, tagged so ``outcome_row`` reports the skip as ``na`` (never
    ``missing``) instead of a plain ``skip`` -- for a capability whose deployment
    flag is structurally off in some environment, not one that merely hasn't run
    yet. See ``ENV_GATED_MARKER``.
    """
    skip = pytest.mark.skipif(condition, reason=reason)
    tag = getattr(pytest.mark, ENV_GATED_MARKER)

    def decorator(fn: _F) -> _F:
        return skip(tag(fn))

    return decorator


@dataclass(frozen=True)
class ScorecardRow:
    """One test's outcome; lane and OS identify a gate problem when present."""

    test: str
    adapter: str
    status: Status
    reason: str | None = None
    lane: str | None = None
    os: str | None = None


def _test_id(nodeid: str) -> str:
    """The test-function nodeid — the cell's ``[adapter]`` param stripped off."""
    return nodeid.split("[", 1)[0]


def _cell_key(nodeid: str) -> tuple[str, str] | None:
    """The ``(test, adapter)`` row key for ``nodeid``, or ``None`` if it names no
    matrix cell (unparametrized, or parametrized by something other than an
    adapter id — e.g. ``test_send_event[thought]``)."""
    test, sep, rest = nodeid.partition("[")
    if not sep:
        return None
    adapter = rest.rstrip("]")
    if adapter not in _ADAPTER_IDS:
        return None
    return test, adapter


def _row_key(nodeid: str) -> tuple[str, str]:
    """Use the full nodeid for bespoke and non-adapter parametrized tests."""
    return _cell_key(nodeid) or (nodeid, SUITE_ADAPTER)


def na_rows(items: Iterable[pytest.Item]) -> dict[tuple[str, str], ScorecardRow]:
    """The ``N/A`` cells the matrix defines: every ``@per_adapter`` exclusion, with reason.

    Excluded adapters have no test node, so their reasons exist only on the marker (shared
    by every surviving cell of the test — reading it off any one is enough). Keyed by
    ``(test, adapter)`` for a disjoint merge with the run outcomes.
    """
    rows: dict[tuple[str, str], ScorecardRow] = {}
    for item in items:
        marker = item.get_closest_marker(PER_ADAPTER_MARKER)
        if marker is None or not marker.args:
            continue
        build = marker.args[0]
        if not isinstance(build, PerAdapter):
            continue
        test = _test_id(item.nodeid)
        for excluded in build.exclude:
            adapter = str(excluded.adapter)
            rows[(test, adapter)] = ScorecardRow(test, adapter, "na", excluded.reason)
    return rows


def _skip_reason(report: pytest.TestReport) -> str | None:
    """The human reason from a skip report's ``longrepr`` (``(path, line, msg)``)."""
    longrepr = report.longrepr
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        return longrepr[2].removeprefix("Skipped: ").strip() or None
    return None


def outcome_row(
    report: pytest.TestReport,
) -> tuple[tuple[str, str], ScorecardRow] | None:
    """Map a report to its matrix or bespoke row.

    Skips tagged by ``env_gated_skip`` are N/A. Any setup, call, or teardown
    failure is a failure; passing setup and teardown reports add no outcome.
    """
    if report.when not in ("setup", "call", "teardown"):
        return None
    key = _row_key(report.nodeid)
    test, adapter = key
    if getattr(report, "outcome", None) == "rerun":
        status = "fail"
        reason = "first attempt required a rerun"
    elif report.skipped:
        reason = _skip_reason(report)
        status: Status = "na" if ENV_GATED_MARKER in report.keywords else "skip"
    elif report.failed:
        status = "fail"
        reason = None
    elif report.when == "call" and report.passed:
        status = "pass"
        reason = None
    else:
        return None  # passing setup/teardown carries no verdict
    return key, ScorecardRow(test, adapter, status, reason)


class ScorecardCollector:
    """A pytest plugin that records cell outcomes and writes the run's scorecard.

    The conftest registers one instance — only when emission is enabled (a path is set),
    so its hooks are unconditional once active — instead of routing session-wide hooks
    through module globals. It owns its state and its output path; the row-building stays
    in the module-level pure functions (``outcome_row`` / ``na_rows``), so ``scorecard``
    is unit-testable without a running session.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = path
        self._outcomes: dict[tuple[str, str], ScorecardRow] = {}

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        row = outcome_row(report)
        if row is not None and (
            row[0] not in self._outcomes or self._outcomes[row[0]].status != "fail"
        ):
            # A later report can never rescue a failed first attempt.
            self._outcomes[row[0]] = row[1]

    def scorecard(self, items: Iterable[pytest.Item]) -> list[ScorecardRow]:
        """Collected outcomes plus explicit N/A rows for excluded adapters."""
        items = list(items)
        rows = dict(self._outcomes)
        rows.update(na_rows(items))
        return sorted(rows.values(), key=lambda row: (row.test, row.adapter))

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        write_json(self.scorecard(session.items), self._path)


def merge(scorecards: Iterable[list[ScorecardRow]]) -> list[ScorecardRow]:
    """Union per-lane scorecards into one grid.

    A cell runs in exactly one lane, so across lanes only one scorecard has a real
    outcome for it and the rest are ``skip``; ``N/A`` is ``N/A`` everywhere. Keeping the
    highest-ranked row per cell surfaces the real result and never lets a ``skip`` hide an
    ``N/A`` — a cell that ran nowhere (its lane never reported) stays ``skip``, visible
    rather than silently dropped.
    """
    best: dict[tuple[str, str], ScorecardRow] = {}
    for card in scorecards:
        for row in card:
            key = (row.test, row.adapter)
            if key not in best or _RANK[row.status] > _RANK[best[key].status]:
                best[key] = row
    return sorted(best.values(), key=lambda row: (row.test, row.adapter))


@dataclass(frozen=True)
class ExpectedCell:
    """A required first-attempt result in each listed lane on its supported OSes."""

    test: str
    adapter: str
    lanes: tuple[str, ...]
    status: Literal["pass", "na"]
    reason: str | None = None


def expected_cells(items: Iterable[pytest.Item]) -> list[ExpectedCell]:
    """Freeze the collected suite's required cells independently of a live run."""
    items = list(items)
    all_lanes = tuple(sorted(known_lane_ids()))
    lane_of = adapter_home_lanes()
    cells: dict[tuple[str, str], ExpectedCell] = {}
    for item in items:
        test, adapter = _row_key(item.nodeid)
        target = _resolve_expected_lane(item, lane_of)
        env_gated = item.get_closest_marker(ENV_GATED_MARKER)
        status: Literal["pass", "na"] = "na" if env_gated else "pass"
        lanes = all_lanes if env_gated or target is None else (target,)
        skipif = item.get_closest_marker("skipif") if env_gated else None
        reason = skipif.kwargs.get("reason") if skipif is not None else None
        cells[(test, adapter)] = ExpectedCell(test, adapter, lanes, status, reason)
    for (test, adapter), row in na_rows(items).items():
        cells[(test, adapter)] = ExpectedCell(
            test, adapter, all_lanes, "na", row.reason
        )
    return sorted(cells.values(), key=lambda cell: (cell.test, cell.adapter))


def supported_oses() -> dict[str, tuple[str, ...]]:
    return {
        str(lane.id): ("ubuntu",) if lane.linux_only else ("ubuntu", "windows")
        for lane in ci_lanes()
    }


class ManifestCollector:
    """Capture pytest's collection after all lane and wiring guards have run."""

    def __init__(self) -> None:
        self.cells: list[ExpectedCell] = []

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.cells = expected_cells(session.items)


def _write_manifest(path: str | Path, *, merge_existing: bool = False) -> None:
    collector = ManifestCollector()
    code = pytest.main(
        [
            "tests/e2e/baseline/",
            "--collect-only",
            "-q",
            "--no-cov",
            "-p",
            "no:rerunfailures",
        ],
        plugins=[collector],
    )
    if code != pytest.ExitCode.OK:
        sys.exit(int(code))
    cells = {(cell.test, cell.adapter): cell for cell in collector.cells}
    if merge_existing and Path(path).exists():
        _oses, prior = _load_manifest(path)
        cells = {(cell.test, cell.adapter): cell for cell in prior} | cells
    Path(path).write_text(
        json.dumps(
            {
                "supported_os": supported_oses(),
                "cells": [
                    asdict(cell)
                    for cell in sorted(
                        cells.values(), key=lambda cell: (cell.test, cell.adapter)
                    )
                ],
            },
            indent=2,
        )
        + "\n"
    )


def _load_manifest(
    path: str | Path,
) -> tuple[dict[str, tuple[str, ...]], list[ExpectedCell]]:
    data = json.loads(Path(path).read_text())
    oses = {lane: tuple(values) for lane, values in data["supported_os"].items()}
    if oses != supported_oses():
        raise ValueError(
            "expected-cell manifest OS support has drifted from the lane registry"
        )
    cells = [ExpectedCell(**cell) for cell in data["cells"]]
    return oses, cells


@dataclass(frozen=True)
class GateResult:
    """CI verdict with each problem tied to its lane and OS."""

    ok: bool
    failing: tuple[ScorecardRow, ...]
    missing: tuple[ScorecardRow, ...]
    unexpected: tuple[ScorecardRow, ...] = ()


def gate_manifest(
    fragments: dict[tuple[str, str], list[ScorecardRow]],
    cells: list[ExpectedCell],
    supported_os: dict[str, tuple[str, ...]],
    selected_lane: str,
    selected_os: str,
    *,
    selected_tests_only: bool = False,
) -> GateResult:
    """Require each declared cell's first result in every selected lane and OS.

    ``selected_tests_only`` gates a ``-k`` scoped run: pytest never collects the
    deselected cells, so only the cells a fragment reports are checked.
    """
    lanes = set(supported_os) if selected_lane == "all" else {selected_lane}
    if not lanes <= supported_os.keys():
        raise ValueError(f"unknown selected lane: {selected_lane}")
    if selected_os not in {"all", "ubuntu", "windows"}:
        raise ValueError(f"unknown selected OS: {selected_os}")
    pairs = {
        (lane, os)
        for lane in lanes
        for os in supported_os[lane]
        if selected_os == "all" or selected_os == os
    }
    if not pairs:
        raise ValueError(f"no supported lane/OS pair for {selected_lane}/{selected_os}")

    failing: list[ScorecardRow] = []
    missing: list[ScorecardRow] = []
    unexpected: list[ScorecardRow] = []
    for lane, os in sorted(pairs):
        actual = {(row.test, row.adapter): row for row in fragments.get((lane, os), [])}
        expected = {
            (cell.test, cell.adapter): cell
            for cell in cells
            if lane in cell.lanes
            and (not selected_tests_only or (cell.test, cell.adapter) in actual)
        }
        for key, cell in expected.items():
            row = actual.get(key)
            if (
                row is None
                or row.status != cell.status
                or (
                    cell.status == "na"
                    and cell.reason is not None
                    and row.reason != cell.reason
                )
            ):
                problem = replace(
                    row
                    if row is not None
                    else ScorecardRow(cell.test, cell.adapter, "skip", "no result"),
                    lane=lane,
                    os=os,
                )
                (
                    failing if row is not None and row.status == "fail" else missing
                ).append(problem)
        for key, row in actual.items():
            if key not in expected and row.status != "skip":
                (failing if row.status == "fail" else unexpected).append(
                    replace(row, lane=lane, os=os)
                )
    for lane, os in sorted(fragments.keys() - pairs):
        unexpected.append(
            ScorecardRow(
                "scorecard fragment",
                SUITE_ADAPTER,
                "fail",
                "outside selected matrix",
                lane=lane,
                os=os,
            )
        )
    return GateResult(
        ok=not (failing or missing or unexpected),
        failing=tuple(failing),
        missing=tuple(missing),
        unexpected=tuple(unexpected),
    )


def gate_summary(result: GateResult, rows: list[ScorecardRow]) -> str:
    """A one-line verdict + totals, meant to sit above the markdown grid."""
    counts = {status: sum(1 for r in rows if r.status == status) for status in _RANK}
    verdict = "PASS" if result.ok else "FAIL"
    line = (
        f"**GATE: {verdict}** — {counts['pass']} passed, {counts['fail']} failed, "
        f"{counts['na']} N/A, {counts['skip']} skipped"
    )
    if not result.ok:
        culprits = sorted(
            f"`{r.lane or '?'}`/`{r.os or '?'}`/"
            f"`{r.test.rsplit('::', 1)[-1]}`/`{r.adapter}`"
            for r in (*result.failing, *result.missing, *result.unexpected)
        )
        line += "\n\nFailing cells: " + ", ".join(culprits)
    return line + "\n"


def _cell_lines(rows: tuple[ScorecardRow, ...]) -> list[str]:
    return [
        f"- `{r.lane or '?'}` / `{r.os or '?'}` / "
        f"`{r.test.rsplit('::', 1)[-1]}` / `{r.adapter}`"
        for r in sorted(rows, key=lambda r: (r.test, r.adapter))
    ]


def digest_body(result: GateResult, rows: list[ScorecardRow]) -> str:
    """A counts table + only the problem cells — no header, no wide grid.

    The email-safe half of :func:`gate_summary`: a full adapter×test grid reads fine
    in a GitHub Actions step summary (full width, GitHub's own renderer) but turns
    into a cramped, unreadable wall in a notification email, so this deliberately
    leaves it out — a caller wanting the full picture links to the run instead. The
    counts render as a small GFM table (not a "·"-joined line): GitHub's notification
    email renders plain GFM — tables, bold, bullets — the same as the web UI, just
    with no `<style>`/inline-CSS support, so a table is the highest-fidelity "glance"
    layout available without a custom HTML email. Also deliberately carries no
    PASS/FAIL header: a matrix-leg crash can override `result.ok`'s verdict, so a
    caller with that broader context should render its own header.
    """
    counts = {status: sum(1 for r in rows if r.status == status) for status in _RANK}
    lines = [
        "| Passed | Failed | N/A | Skipped |",
        "| --- | --- | --- | --- |",
        f"| {counts['pass']} | {counts['fail']} | {counts['na']} | {counts['skip']} |",
    ]
    if result.failing:
        lines += ["", "**Failing**", *_cell_lines(result.failing)]
    if result.missing:
        lines += ["", "**Missing** (lane ran, no result)", *_cell_lines(result.missing)]
    if result.unexpected:
        lines += [
            "",
            "**Unexpected** (not in manifest)",
            *_cell_lines(result.unexpected),
        ]
    return "\n".join(lines) + "\n"


def write_json(rows: list[ScorecardRow], path: str | Path) -> None:
    """Write ``rows`` as a JSON array to ``path`` (creating parent dirs)."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([asdict(row) for row in rows], indent=2) + "\n")


def _load(path: str | Path) -> list[ScorecardRow]:
    return [ScorecardRow(**row) for row in json.loads(Path(path).read_text())]


def to_markdown(rows: list[ScorecardRow]) -> str:
    """A pivot grid (tests × adapters) plus the ``N/A`` reasons — the one-look view."""
    symbol: dict[Status, str] = {"pass": "✅", "fail": "❌", "skip": "⏭️", "na": "N/A"}
    tests = sorted({row.test.rsplit("::", 1)[-1] for row in rows})
    adapters = sorted({row.adapter for row in rows})
    cell = {(row.test.rsplit("::", 1)[-1], row.adapter): row.status for row in rows}

    header = "| test | " + " | ".join(adapters) + " |"
    divider = "| --- " * (len(adapters) + 1) + "|"
    body = [
        "| "
        + test
        + " | "
        + " | ".join(symbol.get(cell.get((test, a), "skip"), "·") for a in adapters)
        + " |"
        for test in tests
    ]
    lines = [header, divider, *body]

    na = [row for row in rows if row.status == "na"]
    if na:
        lines += ["", "**N/A reasons**", ""]
        lines += [
            f"- `{row.test.rsplit('::', 1)[-1]}` / `{row.adapter}` — {row.reason}"
            for row in sorted(na, key=lambda r: (r.test, r.adapter))
        ]
    return "\n".join(lines) + "\n"


def _merge_cmd(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    oses, cells = _load_manifest(args.manifest)
    fragments: dict[tuple[str, str], list[ScorecardRow]] = {}
    for path in args.inputs:
        match = re.fullmatch(r"scorecard-(.+)-(ubuntu|windows)\.json", Path(path).name)
        if match is None:
            parser.error(f"unrecognized lane scorecard name: {path}")
        pair = (match[1], match[2])
        if pair in fragments:
            parser.error(f"duplicate lane scorecard: {pair}")
        fragments[pair] = _load(path)
    rows = merge(fragments.values())
    write_json(rows, args.out)
    try:
        result = gate_manifest(
            fragments,
            cells,
            oses,
            args.selected_lane,
            args.selected_os,
            selected_tests_only=args.selected_tests_only,
        )
    except ValueError as exc:
        parser.error(str(exc))
    if args.markdown:
        Path(args.markdown).write_text(
            gate_summary(result, rows) + "\n" + to_markdown(rows)
        )
    if args.summary:
        Path(args.summary).write_text(digest_body(result, rows))
    logger.info(
        "scorecard: %d cells from %d lane file(s) -> %s (gate: %s)",
        len(rows),
        len(args.inputs),
        args.out,
        "PASS" if result.ok else "FAIL",
    )
    if not result.ok:
        sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    """Write the expected manifest, or merge and gate first-attempt scorecards."""
    parser = argparse.ArgumentParser(prog="scorecard")
    sub = parser.add_subparsers(dest="cmd", required=True)

    merge_cmd = sub.add_parser("merge", help="union per-lane scorecards into one grid")
    merge_cmd.add_argument("inputs", nargs="+", help="per-lane scorecard JSON files")
    merge_cmd.add_argument("--out", required=True, help="combined scorecard.json path")
    merge_cmd.add_argument("--markdown", help="also write a markdown grid to this path")
    merge_cmd.add_argument(
        "--summary",
        help="also write the email-safe digest (counts + only the problem cells, no "
        "grid — see digest_body) to this path",
    )
    merge_cmd.add_argument("--selected-lane", default="all")
    merge_cmd.add_argument("--selected-os", default="all")
    merge_cmd.add_argument(
        "--selected-tests-only",
        action="store_true",
        help="gate only the cells a pytest -k scoped run reported",
    )
    merge_cmd.add_argument(
        "--manifest",
        default=str(Path(__file__).with_name("expected-cells.json")),
    )
    manifest_cmd = sub.add_parser(
        "manifest", help="regenerate the expected-cell manifest"
    )
    manifest_cmd.add_argument(
        "--out",
        default=str(Path(__file__).with_name("expected-cells.json")),
    )
    manifest_cmd.add_argument(
        "--merge-existing",
        action="store_true",
        help="union cells collected under a second optional-dependency extra",
    )

    args = parser.parse_args(argv)
    if args.cmd == "merge":
        _merge_cmd(args, parser)
    else:
        _write_manifest(args.out, merge_existing=args.merge_existing)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
