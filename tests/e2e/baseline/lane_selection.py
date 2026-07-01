"""CI-lane scheduling for baseline collection (the ``BAND_E2E_LANE`` knob).

The collection logic behind ``pytest_collection_modifyitems`` lives here so the
conftest hook stays a one-line delegate. Scheduling is *derived*: a test runs in a
lane iff that lane can host **all** the frameworks the test touches.

* ``item_frameworks`` — the adapter(s) a test is bound to: a matrix cell's
  ``adapter_id`` plus its declared ``peer``, or a ``@with_adapters`` group's set.
* ``item_home_lanes`` — the CI lanes those frameworks live in (from the registry's
  ``ci_lanes`` partition; each adapter has exactly one home lane).

``apply_lane_skips`` scopes a run to the active lane by one rule: a test with an
explicit ``@lane(L)`` assignment runs iff ``L`` is active; otherwise a test whose
frameworks share **one** home lane runs there (and is skip-with-reason'd elsewhere) —
this reproduces single-adapter cell behaviour exactly.

``assert_every_item_is_schedulable`` is the fail-loud guard against a test that would
silently skip in every lane (false green). It reasons about *hosting* — which lanes'
``uv`` extra can install a framework (``ci_lanes.hosting_lanes``), broader than an
adapter's single *home* lane. A test spanning >1 home lane must name a hosting lane
with ``@lane(L)`` (see ``agents.lane``); a test whose frameworks need incompatible
extras (e.g. crewai + a ``dev`` framework) can be hosted by no lane and fails outright,
and an ``@lane`` that does not host all the frameworks is itself an error.
"""

from __future__ import annotations

import pytest

from tests.e2e.baseline.agents import (
    LANE_MARKER,
    PER_ADAPTER_MARKER,
    WITH_ADAPTERS_MARKER,
    PerAdapter,
    WithAdapters,
)
from tests.e2e.baseline.toolkit.ci_lanes import CILane, ci_lanes, hosting_lanes


def _lane_of(lanes: list[CILane]) -> dict[str, str]:
    """Flatten the registry's lane partition to an adapter-id -> home-lane-id map."""
    return {str(adapter): str(cl.id) for cl in lanes for adapter in cl.adapters}


def item_frameworks(item: pytest.Item) -> frozenset[str]:
    """The adapter ids a test is bound to (empty = adapter-agnostic, always kept).

    A matrix cell carries its id as the ``adapter_id`` callspec param and, when it
    declares one, a ``peer`` on the ``per_adapter`` marker; a ``@with_adapters`` test
    carries its adapters on the with-adapters marker. Peers are included so scheduling
    and the schedulability guard see the *second* framework a cross-framework cell
    drives — not just the fanned cell. Tests with neither (provisioning, user-ops, the
    registry guards) target no adapter and run in every lane.
    """
    ids: set[str] = set()
    callspec = getattr(item, "callspec", None)
    if callspec is not None and "adapter_id" in callspec.params:
        ids.add(str(callspec.params["adapter_id"]))
        per = item.get_closest_marker(PER_ADAPTER_MARKER)
        if per is not None:
            build: PerAdapter = per.args[0]
            if build.peer is not None:
                ids.add(str(build.peer))
    marker = item.get_closest_marker(WITH_ADAPTERS_MARKER)
    if marker is not None:
        request: WithAdapters = marker.args[0]
        ids.update(str(adapter) for adapter in request.adapters)
    return frozenset(ids)


def _override_lane(item: pytest.Item) -> str | None:
    """The explicit ``@lane(L)`` assignment on ``item`` (its lane id), or ``None``."""
    marker = item.get_closest_marker(LANE_MARKER)
    if marker is None or not marker.args:
        return None
    return str(marker.args[0])


def _home_lanes(item: pytest.Item, lane_of: dict[str, str]) -> frozenset[str]:
    """The home lanes of ``item``'s frameworks (unknown ids ignored — no KeyError)."""
    return frozenset(lane_of[f] for f in item_frameworks(item) if f in lane_of)


def _require_known_lane(lane: str, lanes: list[CILane]) -> None:
    """Raise ``UsageError`` unless ``lane`` is a CI lane the registry emits."""
    known = {str(cl.id) for cl in lanes}
    if lane not in known:
        raise pytest.UsageError(
            f"BAND_E2E_LANE={lane!r} is not a known CI lane; registry lanes are "
            f"{sorted(known)}"
        )


def _lane_skip_reason(
    item: pytest.Item, lane: str, lane_of: dict[str, str]
) -> str | None:
    """Why ``item`` is skipped in ``lane`` (a one-line reason), or ``None`` to run it.

    The single scheduling rule as a pure decision (no side effects), most-specific first:

    * an explicit ``@lane(L)`` runs iff ``L`` is active;
    * an adapter-agnostic test (no frameworks) runs in every lane;
    * a test whose frameworks share one home lane runs there — single-adapter cells
      included, since ``ci_lanes`` groups by ``adapter_lane``;
    * otherwise it spans >1 home lane with no override — unschedulable, so it is skipped
      here too (``assert_every_item_is_schedulable`` has already failed collection for
      it; this just never lets it run in an arbitrary lane).
    """
    override = _override_lane(item)
    if override is not None:
        if override == lane:
            return None
        return f"assigned to lane {override!r} (@lane), not active lane {lane!r}"
    homes = _home_lanes(item, lane_of)
    if not homes:
        return None
    if len(homes) == 1:
        (home,) = homes
        if home == lane:
            return None
        return f"runs in lane {home!r}, not active lane {lane!r}"
    return f"spans lanes {sorted(homes)} with no @lane override (unschedulable)"


def apply_lane_skips(lane: str, items: list[pytest.Item]) -> None:
    """Scope the run to ``lane`` by skip-marking every item ``_lane_skip_reason`` rejects.

    Thin orchestrator: validate the active lane, then map the pure per-item decision onto
    a skip marker. An empty ``lane`` leaves collection untouched (full matrix, fail-loud)
    — the correct local default.
    """
    if not lane:
        return
    lanes = ci_lanes()
    _require_known_lane(lane, lanes)
    lane_of = _lane_of(lanes)
    for item in items:
        reason = _lane_skip_reason(item, lane, lane_of)
        if reason is not None:
            item.add_marker(pytest.mark.skip(reason=reason))


def assert_every_item_is_schedulable(items: list[pytest.Item]) -> None:
    """Fail collection for any test that no single CI lane can *host*.

    Lanes partition the matrix into separate CI jobs (a ``uv`` extra + optional backend).
    A test runs in a lane only if that lane's extra installs **all** the frameworks it
    touches (``ci_lanes.hosting_lanes``) — *hosting*, which is broader than an adapter's
    single *home* lane (every ``dev`` lane hosts every ``dev`` framework). A test no lane
    can host is skipped in **every** lane, so it never runs yet shows green — the exact
    false-confidence the fail-loud policy forbids. This validates three things in one
    rule (peer-aware — a ``@per_adapter(peer=...)`` cell's peer counts as a framework):

    * an ``@lane(L)`` override must name a lane that actually hosts all the frameworks
      (``L`` in their hosting lanes) — this catches a typo'd, unknown, or wrong-extra
      pin, which would otherwise skip the test everywhere;
    * a test spanning >1 home lane whose frameworks *share* an extra is schedulable but
      ambiguous — it must pick one with ``@lane(L)``;
    * a test whose frameworks need *incompatible* extras (e.g. crewai + a ``dev``
      framework) can be hosted by no lane and no ``@lane`` can rescue it.

    Runs in every collection — lane-scoped or not — so drift fails before CI.
    Single-framework and same-home tests never trip it.
    """
    lane_of = _lane_of(ci_lanes())
    offenders: list[str] = []
    for item in items:
        homes = _home_lanes(item, lane_of)
        override = _override_lane(item)
        if override is not None:
            hosts = hosting_lanes(homes)
            if override not in hosts:
                offenders.append(
                    f"{item.nodeid} pins @lane({override!r}) but no lane hosts all its "
                    f"frameworks there; hosting lanes: {sorted(hosts) or 'none'}"
                )
            continue
        if len(homes) <= 1:
            continue  # single home lane (or adapter-agnostic) — always schedulable
        hosts = hosting_lanes(homes)
        if hosts:
            offenders.append(
                f"{item.nodeid} spans home lanes {sorted(homes)} but shares an extra; "
                f"add @lane(L) with L in {sorted(hosts)}"
            )
        else:
            offenders.append(
                f"{item.nodeid} touches frameworks needing incompatible uv extras "
                f"(home lanes {sorted(homes)}); no single lane can host them"
            )
    if offenders:
        joined = "\n  ".join(offenders)
        raise pytest.UsageError(
            "unschedulable test(s) — each would skip in every CI lane and show green:\n  "
            f"{joined}\nFix: restrict each to one lane's adapters, or add @lane(L) naming "
            "a lane whose uv extra hosts all of the test's frameworks."
        )
