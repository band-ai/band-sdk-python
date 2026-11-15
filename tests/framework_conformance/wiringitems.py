"""Provide synthetic collection items for agent wiring policy tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from tests.e2e.baseline.agent_wiring import assert_agent_fixtures_wired
from tests.e2e.baseline.agents import PER_ADAPTER_MARKER, WITH_ADAPTERS_MARKER


class FakeItem:
    """Minimal ``pytest.Item`` stand-in: only what ``_wiring_error`` reads."""

    def __init__(
        self,
        nodeid: str = "t.py::test",
        *,
        each: bool = False,
        group: bool = False,
        peer_declared: bool = False,
        fixturenames: tuple[str, ...] = (),
        each_count: int = 1,
        group_count: int = 1,
    ) -> None:
        self.nodeid = nodeid
        self.fixturenames = fixturenames
        # name -> the marker(s) applied; a list so a stacked (duplicate) decorator is
        # representable, which is what the "applied more than once" rule reads.
        self._markers: dict[str, list[SimpleNamespace]] = {}
        # `peer_declared` implies @per_adapter; the payload carries a peer= only then, so
        # the guard's `getattr(payload, "peer", None)` distinguishes declared from not.
        if each or peer_declared:
            payload = (
                SimpleNamespace(peer="langgraph")
                if peer_declared
                else SimpleNamespace()
            )
            self._markers[PER_ADAPTER_MARKER] = [
                SimpleNamespace(args=(payload,))
            ] * each_count
        if group:
            self._markers[WITH_ADAPTERS_MARKER] = [
                SimpleNamespace(args=(SimpleNamespace(),))
            ] * group_count

    def get_closest_marker(self, name: str) -> SimpleNamespace | None:
        marks = self._markers.get(name)
        return marks[0] if marks else None

    def iter_markers(self, name: str) -> list[SimpleNamespace]:
        return self._markers.get(name, [])


def wiring_error_reason(item: FakeItem) -> str:
    """The guard's error message for a single offending item."""
    with pytest.raises(pytest.UsageError) as excinfo:
        assert_agent_fixtures_wired([cast(pytest.Item, item)])
    return str(excinfo.value)
