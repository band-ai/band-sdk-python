"""The adapter×test scorecard: pass / fail / skip / N-A (+ reason) in one artifact.

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

The pieces are pure functions so they unit-test without a live platform; the conftest is
a thin hook delegate, and ``python -m tests.e2e.baseline.scorecard merge`` is the
post-run CI step that folds the lanes together.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import pytest

from tests.e2e.baseline.agents import PER_ADAPTER_MARKER, Adapter, PerAdapter

logger = logging.getLogger(__name__)

# Only a registered adapter id names a matrix cell. Other parametrized tests carry
# unrelated params in their nodeid (e.g. ``test_send_event[thought]``), which must not
# be mistaken for adapters when reading outcomes off a report.
_ADAPTER_IDS: frozenset[str] = frozenset(str(adapter) for adapter in Adapter)

# ``na`` = deliberately excluded (with a reason); ``skip`` = collected but not run in this
# lane (lane scoping / E2E disabled). Ranked so a real outcome beats ``skip`` when the
# per-lane scorecards are unioned, and an ``N/A`` is never overwritten by a ``skip``.
Status = Literal["pass", "fail", "skip", "na"]
_RANK: dict[Status, int] = {"skip": 0, "na": 1, "pass": 2, "fail": 3}


@dataclass(frozen=True)
class ScorecardRow:
    """One adapter×test cell: its outcome, and the reason when it is ``N/A``/``skip``."""

    test: str  # nodeid without the ``[adapter]`` param — the test function
    adapter: str
    status: Status
    reason: str | None = None


def _test_id(nodeid: str) -> str:
    """The test-function nodeid — the cell's ``[adapter]`` param stripped off."""
    return nodeid.split("[", 1)[0]


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
    """A pass / fail / skip row for one matrix cell, keyed by ``(test, adapter)``.

    Only matrix cells count: a cell's ``[…]`` param is a registered adapter id, so a
    parametrized test carrying anything else (``test_send_event[thought]``) and the
    unparametrized tests (provisioning, user-ops, the registry guards) return ``None``.
    The verdict comes from the setup and call phases: a skip (lane scoping, E2E disabled,
    or an in-body ``pytest.skip``) is ``skip``; a setup *error* (a failed fixture) or a
    call failure is ``fail``; a passing call is ``pass``. Teardown reports and passing
    setups carry no verdict and are ignored — so the accumulator's last-write-wins keeps
    the call outcome, not a trailing teardown.
    """
    if report.when not in ("setup", "call"):
        return None
    test, sep, rest = report.nodeid.partition("[")
    if not sep:
        return None  # unparametrized — not a matrix cell
    adapter = rest.rstrip("]")
    if adapter not in _ADAPTER_IDS:
        return None  # a non-adapter parametrization (e.g. an event type)
    if report.skipped:
        status: Status = "skip"
        reason = _skip_reason(report)
    elif report.failed:
        status = "fail"
        reason = None
    elif report.when == "call":
        status = "pass"
        reason = None
    else:
        return None  # a passing setup carries no verdict — wait for the call phase
    return (test, adapter), ScorecardRow(test, adapter, status, reason)


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
        if row is not None:
            # Last write wins — a flaky rerun's final report is the cell's real outcome.
            self._outcomes[row[0]] = row[1]

    def scorecard(self, items: Iterable[pytest.Item]) -> list[ScorecardRow]:
        """This run's rows: the collected cells' outcomes plus the ``N/A`` exclusions.

        The two sets are disjoint (an excluded adapter has no node, so no outcome), but
        ``N/A`` is applied last so a marker reason is authoritative if they ever overlap.
        """
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


def main(argv: list[str] | None = None) -> None:
    """CLI: ``merge`` the per-lane scorecards CI uploads into one artifact."""
    parser = argparse.ArgumentParser(prog="scorecard")
    sub = parser.add_subparsers(dest="cmd", required=True)
    merge_cmd = sub.add_parser("merge", help="union per-lane scorecards into one grid")
    merge_cmd.add_argument("inputs", nargs="+", help="per-lane scorecard JSON files")
    merge_cmd.add_argument("--out", required=True, help="combined scorecard.json path")
    merge_cmd.add_argument("--markdown", help="also write a markdown grid to this path")
    args = parser.parse_args(argv)

    rows = merge(_load(path) for path in args.inputs)
    write_json(rows, args.out)
    if args.markdown:
        Path(args.markdown).write_text(to_markdown(rows))
    logger.info(
        "scorecard: %d cells from %d lane file(s) -> %s",
        len(rows),
        len(args.inputs),
        args.out,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
