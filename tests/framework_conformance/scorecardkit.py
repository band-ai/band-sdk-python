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

from tests.e2e.baseline.agents import (
    LANE_MARKER,
    PER_ADAPTER_MARKER,
    ExcludedAdapter,
    PerAdapter,
)
from tests.e2e.baseline.toolkit.ci_lanes import ci_lanes

# --- ExcludedAdapter: a reason is mandatory -----------------------------------------


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


# --- na_rows: marker exclusions become N/A rows -------------------------------------


class ScorecardItem:
    """A ``pytest.Item`` stand-in exposing only what ``na_rows`` reads."""

    def __init__(self, nodeid: str, exclude: tuple[ExcludedAdapter, ...] = ()) -> None:
        self.nodeid = nodeid
        build = PerAdapter(prompt=None, features=None, tools=None, exclude=exclude)
        self._marker = SimpleNamespace(args=(build,))

    def get_closest_marker(self, name: str) -> object | None:
        return self._marker if name == PER_ADAPTER_MARKER else None


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


# --- collector + merge: a run's rows, then the cross-lane union ----------------------


# --- gate: required lane x OS cells ---------------------------------------------------

# Two distinct lanes with at least one adapter each, read off the real registry rather
# than hand-picked ids — a gate case only needs "two different homes", not which ones.
_LANE_A, _LANE_B = [lane for lane in ci_lanes() if lane.adapters][:2]
_ADAPTER_A = str(_LANE_A.adapters[0])
_ADAPTER_B = str(_LANE_B.adapters[0])


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


# --- digest_body: the email-safe half of gate_summary (no header, no grid) ----------
