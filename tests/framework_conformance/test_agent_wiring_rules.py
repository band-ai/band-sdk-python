"""Unit tests for the agent-fixture wiring guard's *policy* (``_wiring_error``).

These exercise every wiring rule fast, through the public ``assert_agent_fixtures_wired``,
with synthetic items — no live platform, no fixtures. They live in ``framework_conformance``
(not ``tests/e2e/**``) so they run in **every** PR, unlike the e2e tree which is skipped
unless ``E2E_TESTS_ENABLED`` is set. The complementary *integration* tests — the real
``pytester`` collection that proves ``@per_adapter``'s ``usefixtures`` keeps an ``agent``-only
test collectable — stay in ``tests/e2e/baseline/guards/test_agent_wiring.py`` (they need the
real fixtures/closure).

Also covers ``@per_adapter(peer=...)`` decoration-time validation.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.e2e.baseline.agent_wiring import assert_agent_fixtures_wired
from tests.e2e.baseline.agents import WITH_ADAPTERS_MARKER, PER_ADAPTER_MARKER


class _FakeItem:
    """Minimal ``pytest.Item`` stand-in: only what ``_wiring_error`` reads."""

    def __init__(
        self,
        nodeid: str = "t.py::test",
        *,
        each: bool = False,
        group: bool = False,
        peer_declared: bool = False,
        fixturenames: tuple[str, ...] = (),
    ) -> None:
        self.nodeid = nodeid
        self.fixturenames = fixturenames
        self._markers: dict[str, SimpleNamespace] = {}
        # `peer_declared` implies @per_adapter; the payload carries a peer= only then, so
        # the guard's `getattr(payload, "peer", None)` distinguishes declared from not.
        if each or peer_declared:
            payload = (
                SimpleNamespace(peer="langgraph")
                if peer_declared
                else SimpleNamespace()
            )
            self._markers[PER_ADAPTER_MARKER] = SimpleNamespace(args=(payload,))
        if group:
            self._markers[WITH_ADAPTERS_MARKER] = SimpleNamespace(
                args=(SimpleNamespace(),)
            )

    def get_closest_marker(self, name: str) -> SimpleNamespace | None:
        return self._markers.get(name)


def _reason(item: _FakeItem) -> str:
    """The guard's error message for a single offending item."""
    with pytest.raises(pytest.UsageError) as excinfo:
        assert_agent_fixtures_wired([item])
    return str(excinfo.value)


# --- valid wirings collect silently -----------------------------------------------------


@pytest.mark.parametrize(
    "item",
    [
        _FakeItem(each=True, fixturenames=("agent", "adapter_id")),
        _FakeItem(each=True, fixturenames=("cell", "adapter_id")),
        _FakeItem(group=True, fixturenames=("agent",)),
        _FakeItem(group=True, fixturenames=("agents",)),
        _FakeItem(
            peer_declared=True, fixturenames=("cell", "peer", "adapter_id")
        ),  # cross-framework peer
        _FakeItem(fixturenames=("resource_manager",)),  # adapter-agnostic test
    ],
    ids=[
        "each+agent",
        "each+cell",
        "group+agent",
        "group+agents",
        "each+cell+peer",
        "agnostic",
    ],
)
def test_valid_wiring_is_allowed(item: _FakeItem) -> None:
    assert_agent_fixtures_wired([item])  # no raise


# --- each rule is enforced ---------------------------------------------------------------


def test_cell_requires_per_adapter() -> None:
    assert "without @per_adapter" in _reason(_FakeItem(fixturenames=("cell",)))


def test_agent_and_cell_are_mutually_exclusive() -> None:
    assert "both `agent` and `cell`" in _reason(
        _FakeItem(each=True, fixturenames=("agent", "cell"))
    )


def test_cell_is_fan_only() -> None:
    assert "fan-only" in _reason(_FakeItem(group=True, fixturenames=("cell",)))


def test_agents_is_group_only() -> None:
    assert "a cell is one adapter" in _reason(
        _FakeItem(each=True, fixturenames=("agents",))
    )


def test_one_topology_per_test() -> None:
    assert "one topology" in _reason(
        _FakeItem(each=True, group=True, fixturenames=("agent",))
    )


def test_agent_requires_a_decorator() -> None:
    assert "no @per_adapter" in _reason(_FakeItem(fixturenames=("agent",)))


def test_peer_requires_per_adapter() -> None:
    assert "without @per_adapter(peer=...)" in _reason(
        _FakeItem(fixturenames=("peer",))
    )


def test_peer_fixture_requires_peer_declaration() -> None:
    # @per_adapter present but no peer= declared, yet the `peer` fixture is requested.
    assert "declares no peer=" in _reason(
        _FakeItem(each=True, fixturenames=("cell", "peer", "adapter_id"))
    )


def test_peer_declaration_requires_peer_fixture() -> None:
    # peer= declared but the `peer` fixture is never requested — the peer would go unused.
    assert "does not request the `peer` fixture" in _reason(
        _FakeItem(peer_declared=True, fixturenames=("cell", "adapter_id"))
    )


def test_no_hand_rolled_matrix() -> None:
    # `adapter_id` requested (or hand-parametrized) without @per_adapter — caught via the
    # fixture closure, so a plain `def test(adapter_id)` is flagged at collection too.
    assert "hand-rolled matrix" in _reason(_FakeItem(fixturenames=("adapter_id",)))


def test_decorator_that_provisions_nothing_is_rejected() -> None:
    # @per_adapter / @with_adapters that requests no agent/agents/cell would run nothing.
    assert "requests none of agent/agents/cell" in _reason(
        _FakeItem(each=True, fixturenames=("adapter_id",))
    )
    assert "requests none of agent/agents/cell" in _reason(
        _FakeItem(group=True, fixturenames=())
    )


def test_all_offenders_are_reported() -> None:
    offenders = [
        _FakeItem("t.py::a", fixturenames=("cell",)),
        _FakeItem("t.py::b", fixturenames=("agent",)),
    ]
    with pytest.raises(pytest.UsageError) as excinfo:
        assert_agent_fixtures_wired(offenders)
    message = str(excinfo.value)
    assert "t.py::a" in message and "t.py::b" in message


# --- @per_adapter(peer=...) declaration-time validation ---------------------------------


def test_peer_must_be_a_live_adapter() -> None:
    """A pending adapter (runs no cells) is rejected as a peer at decoration time."""
    from tests.e2e.baseline.agents import per_adapter
    from tests.e2e.baseline.toolkit.adapters import specs

    pending = [
        s.id for s in specs(include_pending=True) if s.id not in {s.id for s in specs()}
    ]
    assert pending, "expected at least one e2e_pending adapter (e.g. letta)"
    with pytest.raises(ValueError, match="pending adapter"):
        per_adapter(peer=pending[0])
