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
from tests.e2e.baseline.toolkit.adapters import ci_lanes


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
) -> str | None:
    """Why ``targets`` can't run in ``lane`` (skip reason), or ``None`` if in-lane.

    Skip — never fail — only for the deliberate out-of-scope case: an adapter that
    belongs to a *different* lane (another venv, or another backend's job). It is
    covered by its own lane, so skipping here is sharding, not hiding. An *in-lane*
    adapter is left untouched so its ``@requires`` gate still *fails* on a missing
    key/CLI/server (an unwired backend stays loud). ``lane_of`` maps each adapter to
    its lane (from ``ci_lanes``).
    """
    out_of_lane = sorted(t for t in targets if lane_of.get(t) != lane)
    if out_of_lane:
        elsewhere = sorted({lane_of.get(t, "?") for t in out_of_lane})
        return f"{out_of_lane} run in lane(s) {elsewhere}, not active lane {lane!r}"
    return None


def apply_lane_skips(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Scope the run to ``BAND_E2E_LANE`` (a CI lane id), resolved via the registry.

    The lane's adapter set is *derived* (``ci_lanes``), never a hand list — so a
    newly-registered adapter joins its lane with no change here. Every test bound to
    an out-of-lane adapter is marked **skip-with-reason** (visible in the lane's
    report); in-lane tests are untouched, so a missing key/backend still fails via
    the ``@requires`` gate. Adapter-agnostic tests always run. Unset ``BAND_E2E_LANE``
    leaves collection untouched (full matrix, fail-loud) — the correct local default.
    """
    lane = os.environ.get("BAND_E2E_LANE")
    if lane is None:
        return
    lanes = ci_lanes()
    known = {cl.id for cl in lanes}
    if lane not in known:
        raise pytest.UsageError(
            f"BAND_E2E_LANE={lane!r} is not a known CI lane; registry lanes are "
            f"{sorted(str(lane_id) for lane_id in known)}"
        )
    lane_of = {str(a): cl.id for cl in lanes for a in cl.adapters}
    for item in items:
        targets = _item_target_adapters(item)
        if not targets:
            continue
        reason = _lane_skip_reason(targets, lane, lane_of)
        if reason is not None:
            item.add_marker(pytest.mark.skip(reason=reason))
