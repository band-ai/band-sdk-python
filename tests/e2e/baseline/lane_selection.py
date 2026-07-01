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
this reproduces single-adapter cell behaviour exactly. A test whose frameworks span
more than one home lane with no ``@lane`` override can be hosted by no single job, so
``assert_every_item_is_schedulable`` fails collection for it (the fail-loud guard
against a test that would silently skip in every lane — false green). ``@lane`` (see
``agents.lane``) is the override for a genuinely cross-lane test, naming the one lane
whose ``uv`` extra hosts all its frameworks.
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
from tests.e2e.baseline.toolkit.adapters import CILane, ci_lanes


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


def apply_lane_skips(lane: str, items: list[pytest.Item]) -> None:
    """Scope the run to ``lane`` (a CI lane id, from ``settings.run.lane``).

    One derived rule. An explicit ``@lane(L)`` runs iff ``L`` is active. Otherwise a
    test whose frameworks share one home lane runs there and is **skip-with-reason**'d
    in every other lane (a single-adapter cell behaves exactly as before —
    ``ci_lanes`` groups by ``adapter_lane``, so its home lane is its adapter's lane).
    Adapter-agnostic tests always run. A test spanning more than one home lane with no
    override is defensively skipped here, but ``assert_every_item_is_schedulable`` has
    already failed collection for it. An empty ``lane`` leaves collection untouched
    (full matrix, fail-loud) — the correct local default.
    """
    if not lane:
        return
    lanes = ci_lanes()
    known = {str(cl.id) for cl in lanes}
    if lane not in known:
        raise pytest.UsageError(
            f"BAND_E2E_LANE={lane!r} is not a known CI lane; registry lanes are "
            f"{sorted(known)}"
        )
    lane_of = _lane_of(lanes)
    for item in items:
        override = _override_lane(item)
        if override is not None:
            if override != lane:
                item.add_marker(
                    pytest.mark.skip(
                        reason=f"assigned to lane {override!r} (@lane), not active "
                        f"lane {lane!r}"
                    )
                )
            continue
        homes = _home_lanes(item, lane_of)
        if not homes:
            continue  # adapter-agnostic — runs in every lane
        if len(homes) == 1:
            (home,) = tuple(homes)
            if home != lane:
                item.add_marker(
                    pytest.mark.skip(
                        reason=f"runs in lane {home!r}, not active lane {lane!r}"
                    )
                )
            continue
        # Spans >1 home lane with no @lane override: unschedulable (the guard below
        # fails collection for this). Never silently run it in an arbitrary lane.
        item.add_marker(
            pytest.mark.skip(
                reason=f"spans lanes {sorted(homes)} with no @lane override "
                "(unschedulable)"
            )
        )


def assert_every_item_is_schedulable(items: list[pytest.Item]) -> None:
    """Fail collection for any test that no single CI lane can host.

    Lanes partition the matrix into separate CI jobs (a venv or a backend). A test
    whose frameworks live in *different* home lanes can be scheduled by no job — it is
    skipped in **every** lane, so it never runs in CI yet shows green, the exact
    false-confidence the fail-loud policy forbids. This is now peer-aware: a
    ``@per_adapter(peer=...)`` cell whose cell and peer live in different lanes trips
    it just as a cross-lane ``@with_adapters`` group does. The fix is an explicit
    ``@lane(L)`` naming a lane whose ``uv`` extra hosts all the frameworks (or
    restricting the frameworks to one lane). Runs in every collection — lane-scoped or
    not — so drift fails before CI. Single-framework tests never trip it.
    """
    lane_of = _lane_of(ci_lanes())
    offenders: list[str] = []
    for item in items:
        if _override_lane(item) is not None:
            continue  # explicitly assigned; validated against known lanes at scoping
        homes = _home_lanes(item, lane_of)
        if len(homes) > 1:
            spanned = ", ".join(sorted(homes))
            offenders.append(f"{item.nodeid} spans lanes {{{spanned}}}")
    if offenders:
        joined = "\n  ".join(offenders)
        raise pytest.UsageError(
            "test(s) touch frameworks across multiple CI lanes, so no single lane can "
            "host them and they never run in CI (false green). Restrict each to one "
            "lane's adapters, or add @lane(L) naming a lane whose extra hosts all of "
            f"them:\n  {joined}"
        )
