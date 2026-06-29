"""CI-lane scoping for baseline collection (the ``BAND_E2E_LANE`` knob).

The collection logic behind ``pytest_collection_modifyitems`` lives here so the
conftest hook stays a one-line delegate. ``apply_lane_skips`` resolves the active
lane's adapter set from the registry (never a hand-list) and marks every test
bound to an out-of-lane or infra adapter skip-with-reason; in-lane tests are left
untouched so a missing provider key still fails via the ``@requires`` gate.
"""

from __future__ import annotations

import os

import pytest

from tests.e2e.baseline.agents import AGENTS_MARKER, AgentsRequest
from tests.e2e.baseline.toolkit.adapters import ci_lanes, infra_adapters


def _item_target_adapters(item: pytest.Item) -> frozenset[str]:
    """The adapter ids an item is bound to (empty = adapter-agnostic, always kept).

    A matrix cell carries its id as the ``adapter_id`` callspec param; a
    ``@with_agents`` test carries its adapters on the AGENTS_MARKER. Tests with
    neither (provisioning, user-ops, the registry guard) target no adapter and run
    in every lane.
    """
    callspec = getattr(item, "callspec", None)
    if callspec is not None and "adapter_id" in callspec.params:
        return frozenset({str(callspec.params["adapter_id"])})
    marker = item.get_closest_marker(AGENTS_MARKER)
    if marker is not None:
        request: AgentsRequest = marker.args[0]
        return frozenset(str(adapter) for adapter in request.adapters)
    return frozenset()


def _lane_skip_reason(
    targets: frozenset[str],
    lane: str,
    lane_of: dict[str, str],
    infra: frozenset[str],
) -> str | None:
    """Why ``targets`` can't run in ``lane`` (skip reason), or ``None`` if in-lane.

    Skip — never fail — for the two deliberate out-of-scope cases: an infra adapter
    (external backend not wired in CI) or an adapter belonging to a different lane's
    venv. An *in-lane* adapter is left untouched so its ``@requires`` gate still
    *fails* on a missing provider key (misconfig stays loud). ``lane_of`` maps each
    CI-runnable adapter to its lane (from ``ci_lanes``).
    """
    infra_hit = sorted(targets & infra)
    if infra_hit:
        return f"{infra_hit} need an external backend; no CI lane runs them yet"
    out_of_lane = sorted(t for t in targets if lane_of.get(t) != lane)
    if out_of_lane:
        elsewhere = sorted({lane_of.get(t, "?") for t in out_of_lane})
        return f"{out_of_lane} run in lane(s) {elsewhere}, not active lane {lane!r}"
    return None


def apply_lane_skips(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Scope the run to ``BAND_E2E_LANE`` (a ``uv`` extra), resolved via the registry.

    The lane's adapter set is *derived* (``ci_lanes`` / ``infra_adapters``), never a
    hand list — so a newly-registered adapter joins its lane with no change here.
    Every test bound to an out-of-lane or infra adapter is marked **skip-with-reason**
    (visible in the lane's report); in-lane tests are untouched, so a missing
    provider key still fails via the ``@requires`` gate. Adapter-agnostic tests
    always run. Unset ``BAND_E2E_LANE`` leaves collection untouched (full matrix,
    fail-loud) — the correct local default.
    """
    lane = os.environ.get("BAND_E2E_LANE")
    if lane is None:
        return
    lanes = ci_lanes()
    if lane not in lanes:
        raise pytest.UsageError(
            f"BAND_E2E_LANE={lane!r} is not a known CI lane; registry lanes are "
            f"{sorted(lanes)}"
        )
    lane_of = {str(a): extra for extra, ids in lanes.items() for a in ids}
    infra = frozenset(str(a) for a in infra_adapters())
    for item in items:
        targets = _item_target_adapters(item)
        if not targets:
            continue
        reason = _lane_skip_reason(targets, lane, lane_of, infra)
        if reason is not None:
            item.add_marker(pytest.mark.skip(reason=reason))
