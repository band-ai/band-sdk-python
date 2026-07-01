"""CI lane partition: derived from each adapter's requirements (pytest-free).

CI cannot run one job green across the whole fail-loud matrix (crewai conflicts
with the default venv's deps; the external-backend adapters need backends the
plain ``dev`` job doesn't stand up). Each adapter belongs to a *lane* -- a CI job
-- derived from its ``requires`` (via ``adapter_lane`` in ``adapters``), never a
hand-maintained list, so a newly-registered adapter lands in its lane for free and
``assert_every_adapter_has_a_ci_home`` fails loudly if it lands nowhere. A lane
installs one ``uv`` extra (``lane_extra``); the ``backends`` lane stands up
codex/opencode together.

The ``workflow_*`` / ``assert_workflow_*`` helpers tie the ``.github/workflows/
e2e.yml`` lane gates and dispatch dropdown back to this registry-derived partition,
so a renamed/removed lane fails loudly (in the unit suite and the workflow's
``lanes`` job) instead of silently never running.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from tests.e2e.baseline.toolkit.adapters import (
    Adapter,
    adapter_lane,
    specs,
)
from tests.e2e.baseline.toolkit.requirements import (
    DEFAULT_LANE,
    REPO_ROOT,
    Extra,
    Lane,
    lane_extra,
    validate_dep_tables,
)


@dataclass(frozen=True)
class CILane:
    """A CI lane (one job): its id, the ``uv`` extra it installs, and its adapters."""

    id: Lane
    extra: Extra
    adapters: tuple[Adapter, ...]


@lru_cache(maxsize=1)
def ci_lanes() -> tuple[CILane, ...]:
    """Every registered adapter grouped into its CI lane (stable id order).

    The default lane is always present. This is what the CI workflow consumes to
    fan one job per lane (each job installs ``lane.extra`` and provisions its
    backend). An unwired backend lane still appears -- its cells fail loudly until
    the workflow stands the backend up.

    Memoized: the registry is fixed once builders import, and the partition is
    recomputed several times per collection. Returns an immutable tuple so the
    shared cached value is safe.
    """
    # include_pending: a pending adapter still defines its lane (the CI job exists)
    # even though the matrix runs no cells for it.
    by_lane: dict[Lane, list[Adapter]] = {DEFAULT_LANE: []}
    for spec in specs(include_pending=True):  # stable id order
        by_lane.setdefault(adapter_lane(spec), []).append(spec.id)
    return tuple(
        CILane(id=lane, extra=lane_extra(lane), adapters=tuple(ids))
        for lane, ids in sorted(by_lane.items())
    )


def assert_every_adapter_has_a_ci_home() -> None:
    """Fail loudly unless every registered adapter is placed in exactly one CI lane.

    Partner to ``assert_registry_covers_discovered``: that guard ensures a new
    adapter is *registered*; this one ensures it is *placed*. Building ``ci_lanes()``
    also validates the Dep table and surfaces a mis-specified adapter early (an
    unspecified ``Dep`` raises in ``dep_lane``; two distinct lanes raise in
    ``adapter_lane``), so a new adapter cannot silently vanish from CI.
    """
    validate_dep_tables()
    placed = {a for lane in ci_lanes() for a in lane.adapters}
    unplaced = {spec.id for spec in specs(include_pending=True)} - placed
    if unplaced:
        raise AssertionError(
            "adapters not placed in any CI lane (ci_lanes must cover the "
            f"registry): {sorted(str(a) for a in unplaced)}"
        )


def hosting_lanes(home_lanes: frozenset[str]) -> frozenset[str]:
    """CI lane ids whose ``uv`` extra can install *every* framework whose home lane is
    in ``home_lanes`` — empty if none can (the frameworks need incompatible extras, e.g.
    crewai + a ``dev``-extra framework); empty ``home_lanes`` → every lane.

    A lane *hosts* a framework iff they share a ``uv`` extra
    (``lane_extra(lane) == lane_extra(home)``): every ``dev`` lane installs every
    ``dev`` framework, and ``dev-crewai`` installs only the crewai stack. Hosting is thus
    broader than — and distinct from — an adapter's single *home* lane. Conservative and
    correct given crewai is the only venv conflict, and derived purely from
    ``lane_extra`` + the home-lane partition (no hand-maintained table).
    """
    lanes = ci_lanes()
    if not home_lanes:
        return frozenset(str(cl.id) for cl in lanes)
    extras = {lane_extra(Lane(home)) for home in home_lanes}
    if len(extras) != 1:  # frameworks need incompatible extras — no lane hosts them all
        return frozenset()
    (extra,) = extras
    return frozenset(str(cl.id) for cl in lanes if cl.extra == extra)


# The e2e workflow (REPO_ROOT is the single source of the checkout-depth assumption).
_E2E_WORKFLOW = REPO_ROOT / ".github/workflows/e2e.yml"
# A `matrix.lane == 'x'` / `!= "x"` gate literal in the workflow (either quote style).
_LANE_GATE_RE = re.compile(r"""matrix\.lane\s*[!=]=\s*["']([^"']+)["']""")


def workflow_lane_gate_ids(workflow_path: Path = _E2E_WORKFLOW) -> set[str]:
    """The lane ids referenced by ``matrix.lane`` gates in the e2e workflow."""
    return set(_LANE_GATE_RE.findall(workflow_path.read_text(encoding="utf-8")))


def assert_workflow_lane_gates_known(workflow_path: Path = _E2E_WORKFLOW) -> None:
    """Fail loudly if a workflow ``matrix.lane`` gate names a lane the registry
    doesn't emit.

    Lanes are derived from the registry (``ci_lanes``), so a backend setup step
    gated on a renamed/removed lane id is never true and would *silently* never
    run. This guard ties the workflow's lane gates back to the registry so that
    drift fails loudly (in the unit suite and the workflow's ``lanes`` job) instead.
    """
    known = {str(cl.id) for cl in ci_lanes()}
    unknown = workflow_lane_gate_ids(workflow_path) - known
    if unknown:
        raise AssertionError(
            "e2e.yml has matrix.lane gate(s) for lane id(s) the registry does not "
            f"emit (the gated step would never run): {sorted(unknown)}; known "
            f"lanes: {sorted(known)}"
        )


def workflow_lane_options(workflow_path: Path = _E2E_WORKFLOW) -> set[str]:
    """The ``workflow_dispatch`` ``lane`` dropdown options in the e2e workflow.

    GitHub ``choice`` inputs require a *static* options list, so this dropdown is
    the one lane list that can't be derived from the registry at runtime — it is
    hand-maintained and kept honest by ``assert_workflow_lane_options_match_registry``.
    """
    doc = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    # PyYAML parses the bare ``on:`` key as the boolean ``True``, not the string.
    trigger = doc[True]["workflow_dispatch"]
    return set(trigger["inputs"]["lane"]["options"])


def assert_workflow_lane_options_match_registry(
    workflow_path: Path = _E2E_WORKFLOW,
) -> None:
    """Fail loudly if the ``lane`` dropdown drifts from the registry's lanes.

    The dropdown must list exactly ``{registry lanes} | {"all"}``. A registry lane
    *missing* from it can never be dispatch-selected (only ``all`` reaches it); a
    *stale* option that's no longer a lane runs nothing. Unlike the runtime
    ``selected not in known`` check (which only fires when someone picks the bad
    option), this runs in the unit suite, so drift fails on every PR.
    """
    expected = {str(cl.id) for cl in ci_lanes()} | {"all"}
    options = workflow_lane_options(workflow_path)
    missing = expected - options
    stale = options - expected
    if missing or stale:
        raise AssertionError(
            "e2e.yml workflow_dispatch `lane` options drifted from the registry — "
            f"add to the dropdown: {sorted(missing)}; remove (no such lane): "
            f"{sorted(stale)}. Options must be {{registry lanes}} | {{'all'}}."
        )
