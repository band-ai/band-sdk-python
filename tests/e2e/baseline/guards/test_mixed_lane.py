"""Guard smoke: a ``@with_adapters`` test spanning >1 CI lane fails collection.

Such a test is skipped in *every* lane (each lane drops it for its out-of-lane
adapters), so it never runs in CI yet shows green — the false-confidence the
fail-loud policy forbids. ``assert_no_unschedulable_mixed_lane`` turns that into a
loud collection error unless the test opts out with ``@pytest.mark.mixed_lane``.

Drives the guard with synthetic items (no live platform, no construction), and
derives the cross-lane adapter pair from the live registry so a rename can't rot
the test into checking nothing.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.e2e.baseline.agents import WITH_ADAPTERS_MARKER, WithAdapters
from tests.e2e.baseline.lane_selection import (
    MIXED_LANE_MARKER,
    assert_no_unschedulable_mixed_lane,
)
from tests.e2e.baseline.toolkit.adapters import Adapter, ci_lanes


def _two_adapters_in_different_lanes() -> tuple[Adapter, Adapter]:
    """One adapter from each of two distinct, populated CI lanes (registry-derived)."""
    populated = [lane for lane in ci_lanes() if lane.adapters]
    if len(populated) < 2:
        pytest.skip("need >=2 populated CI lanes to exercise the mixed-lane guard")
    return populated[0].adapters[0], populated[1].adapters[0]


def _two_adapters_in_same_lane() -> tuple[Adapter, Adapter] | None:
    """Two adapters sharing one lane, or ``None`` if no lane has two."""
    for lane in ci_lanes():
        if len(lane.adapters) >= 2:
            return lane.adapters[0], lane.adapters[1]
    return None


class _FakeItem:
    """Minimal pytest.Item stand-in: only what ``_item_target_adapters`` reads."""

    def __init__(
        self,
        nodeid: str,
        *,
        agents: tuple[Adapter, ...] | None = None,
        adapter_id: str | None = None,
        mixed_lane: bool = False,
    ) -> None:
        self.nodeid = nodeid
        self._markers: dict[str, SimpleNamespace] = {}
        if agents is not None:
            req = WithAdapters(adapters=agents, prompt=None, features=None, tools=None)
            self._markers[WITH_ADAPTERS_MARKER] = SimpleNamespace(args=(req,))
        if mixed_lane:
            self._markers[MIXED_LANE_MARKER] = SimpleNamespace(args=())
        if adapter_id is not None:
            self.callspec = SimpleNamespace(params={"adapter_id": adapter_id})

    def get_closest_marker(self, name: str) -> SimpleNamespace | None:
        return self._markers.get(name)


def test_cross_lane_with_adapters_fails_collection() -> None:
    """A @with_adapters test spanning two lanes raises a UsageError naming the lanes."""
    a, b = _two_adapters_in_different_lanes()
    item = _FakeItem("tests/e2e/baseline/x.py::test_cross", agents=(a, b))
    with pytest.raises(pytest.UsageError) as excinfo:
        assert_no_unschedulable_mixed_lane([item])
    msg = str(excinfo.value)
    assert "test_cross" in msg
    assert "multiple CI lanes" in msg


def test_mixed_lane_marker_opts_out() -> None:
    """The opt-out marker lets a deliberately local-only cross-lane test through."""
    a, b = _two_adapters_in_different_lanes()
    item = _FakeItem(
        "tests/e2e/baseline/x.py::test_local", agents=(a, b), mixed_lane=True
    )
    assert_no_unschedulable_mixed_lane([item])  # no raise


def test_same_lane_with_adapters_is_allowed() -> None:
    """Two adapters in one lane are schedulable, so the guard stays silent."""
    pair = _two_adapters_in_same_lane()
    if pair is None:
        pytest.skip("no CI lane has two adapters to exercise the same-lane case")
    item = _FakeItem("tests/e2e/baseline/x.py::test_same", agents=pair)
    assert_no_unschedulable_mixed_lane([item])  # no raise


def test_matrix_cell_never_trips_the_guard() -> None:
    """A matrix cell targets a single adapter, so it can never span lanes."""
    a, _ = _two_adapters_in_different_lanes()
    item = _FakeItem("tests/e2e/baseline/m.py::test_matrix", adapter_id=str(a))
    assert_no_unschedulable_mixed_lane([item])  # no raise


def test_adapter_agnostic_item_is_ignored() -> None:
    """A test bound to no adapter (provisioning, user-ops) is always schedulable."""
    item = _FakeItem("tests/e2e/baseline/p.py::test_agnostic")
    assert_no_unschedulable_mixed_lane([item])  # no raise
