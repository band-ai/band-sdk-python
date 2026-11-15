"""Enforce agent fixture topology and adapter staging policy."""

from __future__ import annotations

from dataclasses import replace
from typing import cast
from unittest.mock import patch

import pytest

from tests.e2e.baseline.agent_wiring import assert_agent_fixtures_wired
from tests.e2e.baseline.agents import (
    PerAdapter,
    WithAdapters,
    per_adapter,
)
from tests.e2e.baseline.toolkit import adapters as adapters_module
from tests.e2e.baseline.toolkit.adapters import Adapter, spec_for, specs
from tests.framework_conformance.wiringitems import FakeItem, wiring_error_reason

# --- e2e_pending allowlist ----------------------------------------------------------------

# Pending is a short-lived staging state for an adapter whose backend isn't
# CI-wired yet: it keeps the adapter's CI lane defined while running zero matrix
# cells, which must never become a quiet way to dodge E2E. Adding an adapter here
# requires editing this allowlist (naming it, deliberately, in review) and a
# follow-up that takes its lane live.
EXPECTED_PENDING_ADAPTERS: frozenset[str] = frozenset()


class TestAgentWiringRules:
    # --- valid wirings collect silently -----------------------------------------------------

    @pytest.mark.parametrize(
        "item",
        [
            FakeItem(each=True, fixturenames=("agent", "adapter_id")),
            FakeItem(each=True, fixturenames=("cell", "adapter_id")),
            FakeItem(group=True, fixturenames=("agent",)),
            FakeItem(group=True, fixturenames=("agents",)),
            FakeItem(
                peer_declared=True, fixturenames=("cell", "peer", "adapter_id")
            ),  # cross-framework peer
            FakeItem(fixturenames=("resource_manager",)),  # adapter-agnostic test
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
    def test_valid_wiring_is_allowed(self, item: FakeItem) -> None:
        assert_agent_fixtures_wired([cast(pytest.Item, item)])

    # --- each rule is enforced ---------------------------------------------------------------

    def test_cell_requires_per_adapter(self) -> None:
        assert "without @per_adapter" in wiring_error_reason(
            FakeItem(fixturenames=("cell",))
        )

    def test_agent_and_cell_are_mutually_exclusive(self) -> None:
        assert "both `agent` and `cell`" in wiring_error_reason(
            FakeItem(each=True, fixturenames=("agent", "cell"))
        )

    def test_cell_is_fan_only(self) -> None:
        assert "fan-only" in wiring_error_reason(
            FakeItem(group=True, fixturenames=("cell",))
        )

    def test_agents_is_group_only(self) -> None:
        assert "a cell is one adapter" in wiring_error_reason(
            FakeItem(each=True, fixturenames=("agents",))
        )

    def test_one_topology_per_test(self) -> None:
        assert "one topology" in wiring_error_reason(
            FakeItem(each=True, group=True, fixturenames=("agent",))
        )

    def test_per_adapter_applied_once(self) -> None:
        # A stacked @per_adapter — get_closest_marker would silently keep only one.
        assert "@per_adapter applied more than once" in wiring_error_reason(
            FakeItem(each=True, each_count=2, fixturenames=("agent", "adapter_id"))
        )

    def test_with_adapters_applied_once(self) -> None:
        assert "@with_adapters applied more than once" in wiring_error_reason(
            FakeItem(group=True, group_count=2, fixturenames=("agent",))
        )

    def test_agent_requires_a_decorator(self) -> None:
        assert "no @per_adapter" in wiring_error_reason(
            FakeItem(fixturenames=("agent",))
        )

    def test_peer_requires_per_adapter(self) -> None:
        assert "without @per_adapter(peer=...)" in wiring_error_reason(
            FakeItem(fixturenames=("peer",))
        )

    def test_peer_fixture_requires_peer_declaration(self) -> None:
        # @per_adapter present but no peer= declared, yet the `peer` fixture is requested.
        assert "declares no peer=" in wiring_error_reason(
            FakeItem(each=True, fixturenames=("cell", "peer", "adapter_id"))
        )

    def test_peer_declaration_requires_peer_fixture(self) -> None:
        # peer= declared but the `peer` fixture is never requested — the peer would go unused.
        assert "does not request the `peer` fixture" in wiring_error_reason(
            FakeItem(peer_declared=True, fixturenames=("cell", "adapter_id"))
        )

    def test_no_hand_rolled_matrix(self) -> None:
        # `adapter_id` requested (or hand-parametrized) without @per_adapter — caught via the
        # fixture closure, so a plain `def test(adapter_id)` is flagged at collection too.
        assert "hand-rolled matrix" in wiring_error_reason(
            FakeItem(fixturenames=("adapter_id",))
        )

    def test_decorator_that_provisions_nothing_is_rejected(self) -> None:
        # @per_adapter / @with_adapters that requests no agent/agents/cell would run nothing.
        assert "requests none of agent/agents/cell" in wiring_error_reason(
            FakeItem(each=True, fixturenames=("adapter_id",))
        )
        assert "requests none of agent/agents/cell" in wiring_error_reason(
            FakeItem(group=True, fixturenames=())
        )

    def test_from_node_raises_when_the_decorator_is_missing(self) -> None:
        """A missing decorator fails loud with the caller's hint, not a downstream error."""

        with pytest.raises(pytest.UsageError, match="requires @with_adapters"):
            WithAdapters.from_node(
                cast(pytest.Item, FakeItem()), hint="requires @with_adapters"
            )

    def test_from_node_raises_on_a_wrong_payload_type(self) -> None:
        """A marker whose arg is not the expected payload (e.g. a raw pytest.mark) is caught
        by the isinstance check — a clear UsageError, not an AttributeError deep in a fixture."""

        with pytest.raises(pytest.UsageError):
            PerAdapter.from_node(
                cast(pytest.Item, FakeItem(each=True))
            )  # FakeItem carries a SimpleNamespace

    def test_all_offenders_are_reported(self) -> None:
        offenders = [
            FakeItem("t.py::a", fixturenames=("cell",)),
            FakeItem("t.py::b", fixturenames=("agent",)),
        ]
        with pytest.raises(pytest.UsageError) as excinfo:
            assert_agent_fixtures_wired([cast(pytest.Item, item) for item in offenders])
        message = str(excinfo.value)
        assert "t.py::a" in message and "t.py::b" in message

    # --- @per_adapter(peer=...) declaration-time validation ---------------------------------

    def test_peer_must_be_a_live_adapter(self) -> None:
        """A pending adapter (runs no cells) is rejected as a peer at decoration time.

        Synthesizes the pending state by patching a live adapter's registry entry,
        so the guard stays testable when (as expected) no real adapter is pending.
        """

        pending_spec = replace(
            spec_for(Adapter.LANGGRAPH), e2e_pending="synthetic: backend not CI-wired"
        )
        with (
            patch.dict(adapters_module._REGISTRY, {Adapter.LANGGRAPH: pending_spec}),
            pytest.raises(ValueError, match="pending adapter"),
        ):
            per_adapter(peer=Adapter.LANGGRAPH)

    def test_pending_adapters_match_the_allowlist(self) -> None:
        """The e2e_pending set equals the explicit staging allowlist."""

        pending = {
            str(spec.id): spec.e2e_pending
            for spec in specs(include_pending=True)
            if spec.e2e_pending
        }
        assert set(pending) == EXPECTED_PENDING_ADAPTERS, (
            f"e2e_pending adapters drifted from the allowlist: {pending}. "
            "Pending is a deliberate, short-lived staging state — update "
            "EXPECTED_PENDING_ADAPTERS alongside the registry change, state the "
            "reason in the registration's e2e_pending string, and file the "
            "live-lane follow-up."
        )
