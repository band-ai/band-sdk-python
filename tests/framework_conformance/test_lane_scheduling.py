"""Unit guards for the baseline toolkit's derived CI-lane scheduling.

Pure-function tests (no live platform), so they run in the ordinary unit suite on
every PR rather than only in the manually-triggered E2E job — the lane logic is
load-bearing for CI sharding and a regression here silently re-shards or hides tests.

Covered:
* single-adapter cells schedule exactly by their home lane (matrix-invariance — the
  renovation must not change how the existing matrix shards);
* the schedulability guard fails a test whose frameworks span >1 home lane, is
  peer-aware (a ``@per_adapter(peer=...)`` cross-lane cell trips it too), and is
  satisfied by an explicit ``@lane(L)`` override;
* ``@per_adapter(peer=...)`` folds the peer's requirements into each cell's single
  ``requires`` mark (a second stacked mark would be dropped by ``get_closest_marker``);
* ``tools=[]`` builds no custom tools (withholding a tool from an agent must stick).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.e2e.baseline.agents import (
    LANE_MARKER,
    PER_ADAPTER_MARKER,
    WITH_ADAPTERS_MARKER,
    Adapter,
    Lane,
    PerAdapter,
    WithAdapters,
    adapter_params,
)
from tests.e2e.baseline.lane_selection import (
    apply_lane_skips,
    assert_every_item_is_schedulable,
)
from tests.e2e.baseline.requires import MARKER as REQUIRES_MARKER
from tests.e2e.baseline.toolkit.adapters import (
    _custom_tool_defs,
    adapter_lane,
    spec_for,
    specs,
)
from tests.e2e.baseline.toolkit.ci_lanes import ci_lanes, hosting_lanes
from tests.e2e.baseline.toolkit.deps import Extra


class _FakeItem:
    """Minimal ``pytest.Item`` stand-in for the collection hooks.

    Models only what ``item_frameworks`` / ``_override_lane`` read (callspec params,
    the per-adapter / with-adapters / lane markers) and records ``add_marker`` skips so
    a test can assert whether ``apply_lane_skips`` skipped it.
    """

    def __init__(
        self,
        nodeid: str,
        *,
        agents: tuple[Adapter, ...] | None = None,
        adapter_id: str | None = None,
        peer: Adapter | None = None,
        assigned_lane: Lane | None = None,
    ) -> None:
        self.nodeid = nodeid
        self._markers: dict[str, SimpleNamespace] = {}
        self._added: list[object] = []
        if agents is not None:
            req = WithAdapters(adapters=agents, prompt=None, features=None, tools=None)
            self._markers[WITH_ADAPTERS_MARKER] = SimpleNamespace(args=(req,))
        if adapter_id is not None:
            self.callspec = SimpleNamespace(params={"adapter_id": adapter_id})
            build = PerAdapter(prompt=None, features=None, tools=None, peer=peer)
            self._markers[PER_ADAPTER_MARKER] = SimpleNamespace(args=(build,))
        if assigned_lane is not None:
            self._markers[LANE_MARKER] = SimpleNamespace(args=(assigned_lane,))

    def get_closest_marker(self, name: str) -> SimpleNamespace | None:
        return self._markers.get(name)

    def add_marker(self, marker: object) -> None:
        self._added.append(marker)

    @property
    def skipped(self) -> bool:
        return any(getattr(m, "name", None) == "skip" for m in self._added)


def _two_adapters_in_different_lanes() -> tuple[Adapter, Adapter]:
    populated = [lane for lane in ci_lanes() if lane.adapters]
    if len(populated) < 2:
        pytest.skip("need >=2 populated CI lanes to exercise the schedulability guard")
    return populated[0].adapters[0], populated[1].adapters[0]


# --- matrix-invariance: single-adapter cells shard exactly by their home lane --------


def test_single_adapter_cells_schedule_by_home_lane() -> None:
    """For every registered adapter and every lane, a lone cell runs iff active==home."""
    lane_ids = [str(cl.id) for cl in ci_lanes()]
    for spec in specs(include_pending=True):
        home = str(adapter_lane(spec))
        for active in lane_ids:
            item = _FakeItem(f"m.py::test[{spec.id}]", adapter_id=str(spec.id))
            apply_lane_skips(active, [item])
            assert item.skipped == (home != active), (
                f"{spec.id} active={active} home={home}: skipped={item.skipped}"
            )


def test_adapter_agnostic_item_runs_in_every_lane() -> None:
    for cl in ci_lanes():
        item = _FakeItem("p.py::test_agnostic")
        apply_lane_skips(str(cl.id), [item])
        assert not item.skipped


def test_unknown_active_lane_is_a_usage_error() -> None:
    with pytest.raises(pytest.UsageError):
        apply_lane_skips("no-such-lane", [_FakeItem("x.py::t", adapter_id="anthropic")])


# --- schedulability guard: cross-lane spans fail unless assigned --------------------


def test_cross_lane_with_adapters_fails_collection() -> None:
    a, b = _two_adapters_in_different_lanes()
    item = _FakeItem("x.py::test_cross", agents=(a, b))
    with pytest.raises(pytest.UsageError, match="unschedulable"):
        assert_every_item_is_schedulable([item])


def test_cross_lane_peer_fails_collection() -> None:
    """Peer-aware: a cell whose peer lives in another lane trips the guard too."""
    a, b = _two_adapters_in_different_lanes()
    item = _FakeItem("x.py::test_peer", adapter_id=str(a), peer=b)
    with pytest.raises(pytest.UsageError, match="unschedulable"):
        assert_every_item_is_schedulable([item])


def test_lane_override_makes_cross_lane_schedulable() -> None:
    a, b = _two_adapters_in_different_lanes()
    item = _FakeItem(
        "x.py::test_ok",
        adapter_id=str(a),
        peer=b,
        assigned_lane=adapter_lane(spec_for(a)),
    )
    assert_every_item_is_schedulable([item])  # no raise


def test_same_lane_peer_is_schedulable() -> None:
    """A peer in the cell's own lane never spans (the cross-framework/same-lane case)."""
    item = _FakeItem("x.py::test_same", adapter_id="anthropic", peer=Adapter.CLAUDE_SDK)
    assert_every_item_is_schedulable([item])  # both core
    # ...and it runs in core, skips elsewhere.
    core = _FakeItem("x.py::t", adapter_id="anthropic", peer=Adapter.CLAUDE_SDK)
    apply_lane_skips("core", [core])
    assert not core.skipped


def test_lane_override_runs_only_in_assigned_lane() -> None:
    a, b = _two_adapters_in_different_lanes()
    home_a = adapter_lane(spec_for(a))
    run = _FakeItem("x.py::t", adapter_id=str(a), peer=b, assigned_lane=home_a)
    apply_lane_skips(str(home_a), [run])
    assert not run.skipped
    other = next(str(cl.id) for cl in ci_lanes() if str(cl.id) != str(home_a))
    skip = _FakeItem("x.py::t", adapter_id=str(a), peer=b, assigned_lane=home_a)
    apply_lane_skips(other, [skip])
    assert skip.skipped


# --- hosting: which lanes a framework set can run in, and @lane validation -----------


def test_hosting_lanes_truth_table() -> None:
    """A dev home hosts in every dev lane; incompatible extras host nowhere; empty→all."""
    dev_lanes = frozenset(str(cl.id) for cl in ci_lanes() if cl.extra == Extra.DEV)
    all_lanes = frozenset(str(cl.id) for cl in ci_lanes())
    core_home = str(adapter_lane(spec_for(Adapter.ANTHROPIC)))  # 'core' (dev extra)
    crewai_home = str(adapter_lane(spec_for(Adapter.CREWAI)))  # 'crewai' (dev-crewai)
    assert hosting_lanes(frozenset({core_home})) == dev_lanes
    assert hosting_lanes(frozenset()) == all_lanes
    assert hosting_lanes(frozenset({core_home, crewai_home})) == frozenset()


def test_lane_override_must_name_a_hosting_lane() -> None:
    """A real-but-non-hosting @lane target (wrong extra) fails the guard — finding 1.

    agno (core) + gemini (google) share the ``dev`` extra; ``crewai`` is ``dev-crewai``
    and cannot host them, so pinning @lane(crewai) is a wrong-but-real lane.
    """
    item = _FakeItem(
        "x.py::t", adapter_id="agno", peer=Adapter.GEMINI, assigned_lane=Lane.CREWAI
    )
    with pytest.raises(pytest.UsageError, match="no lane hosts all its frameworks"):
        assert_every_item_is_schedulable([item])


def test_incompatible_extras_are_unschedulable() -> None:
    """crewai + a dev framework need different extras — no lane hosts them; no @lane helps."""
    item = _FakeItem("x.py::t", adapter_id="anthropic", peer=Adapter.CREWAI)
    with pytest.raises(pytest.UsageError, match="incompatible uv extras"):
        assert_every_item_is_schedulable([item])
    pinned = _FakeItem(
        "x.py::t", adapter_id="anthropic", peer=Adapter.CREWAI, assigned_lane=Lane.CORE
    )
    with pytest.raises(pytest.UsageError, match="no lane hosts all its frameworks"):
        assert_every_item_is_schedulable([pinned])


def test_same_extra_cross_home_needs_lane_then_passes() -> None:
    """agno (core) + gemini (google) share the dev extra: ambiguous → needs @lane, which fixes it."""
    bare = _FakeItem("x.py::t", adapter_id="agno", peer=Adapter.GEMINI)
    with pytest.raises(pytest.UsageError, match="add @lane"):
        assert_every_item_is_schedulable([bare])
    pinned = _FakeItem(
        "x.py::t", adapter_id="agno", peer=Adapter.GEMINI, assigned_lane=Lane.GOOGLE
    )
    assert_every_item_is_schedulable([pinned])  # google hosts both dev frameworks


# --- dep gating: peer requirements fold into ONE merged mark -------------------------


def test_peer_deps_fold_into_single_requires_mark() -> None:
    """adapter_params(peer=X) → exactly one requires mark == dedup(cell ∪ peer) deps."""
    params = adapter_params(include={Adapter.ANTHROPIC}, peer=Adapter.LANGGRAPH)
    (param,) = params
    requires_marks = [m for m in param.marks if m.name == REQUIRES_MARKER]
    assert len(requires_marks) == 1, "peer deps must ride on ONE mark, not a second one"
    got = set(requires_marks[0].args[0])
    expected = set(spec_for(Adapter.ANTHROPIC).requires) | set(
        spec_for(Adapter.LANGGRAPH).requires
    )
    assert got == expected


def test_no_peer_leaves_requires_mark_unchanged() -> None:
    (param,) = adapter_params(include={Adapter.ANTHROPIC})
    requires_marks = [m for m in param.marks if m.name == REQUIRES_MARKER]
    assert len(requires_marks) == 1
    assert set(requires_marks[0].args[0]) == set(spec_for(Adapter.ANTHROPIC).requires)


# --- withholding a tool sticks ------------------------------------------------------


def test_empty_tools_builds_no_custom_tools() -> None:
    """tools=[] must resolve to no custom tools (withholding a tool from A must stick)."""
    assert _custom_tool_defs([]) is None
    assert _custom_tool_defs(None) is None
