"""Pytest item doubles for baseline lane-scheduling guards."""

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
)
from tests.e2e.baseline.toolkit.ci_lanes import ci_lanes


class FakeItem:
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
