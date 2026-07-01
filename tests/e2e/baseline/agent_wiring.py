"""Collection-time guard for the agent-fixture / decorator pairing.

Fails collection (before any live agent is provisioned) when a test wires the agent
fixtures in a way that can't work or that bypasses the one-vocabulary rule:

* ``cell`` needs ``@per_adapter``; ``agent`` / ``agents`` need one of the two decorators;
* ``agents`` is group-only, ``cell`` is fan-only;
* a test picks exactly one topology (not both decorators) and one provisioning mode
  (not both ``agent`` and ``cell``);
* nobody hand-rolls a matrix by parametrizing ``adapter_id`` without ``@per_adapter``.

Sibling of ``lane_selection.assert_no_unschedulable_mixed_lane``; both run from the
baseline ``pytest_collection_modifyitems`` hook.
"""

from __future__ import annotations

import pytest

from tests.e2e.baseline.agents import WITH_ADAPTERS_MARKER, PER_ADAPTER_MARKER


def _wiring_error(item: pytest.Item) -> str | None:
    """The wiring violation on ``item`` (a one-line reason), or ``None`` if wired right."""
    each = item.get_closest_marker(PER_ADAPTER_MARKER) is not None
    group = item.get_closest_marker(WITH_ADAPTERS_MARKER) is not None
    fixtures = set(getattr(item, "fixturenames", ()))

    wants_agent = "agent" in fixtures
    wants_agents = "agents" in fixtures
    wants_cell = "cell" in fixtures

    # Order is load-bearing: most-fundamental violation first, so (e.g.) a both-decorators
    # test reports "pick one topology" rather than a downstream fixture-pairing message.
    if each and group:
        return "has both @per_adapter and @with_adapters — pick one topology"
    if wants_agent and wants_cell:
        return "requests both `agent` and `cell` — pick one provisioning mode"
    if wants_cell:
        if group:
            return (
                "requests `cell` under @with_adapters (fan-only) — use `agent`/`agents`"
            )
        if not each:
            return "requests `cell` without @per_adapter"
    if wants_agents and each:
        return (
            "requests `agents` under @per_adapter (a cell is one adapter) — use `agent`"
        )
    if (wants_agent or wants_agents) and not (each or group):
        return (
            "requests `agent`/`agents` with no @per_adapter / @with_adapters decorator"
        )
    # `adapter_id` is @per_adapter's internal parametrize target: requesting or
    # hand-parametrizing it without the decorator is a hand-rolled matrix. Keyed on the
    # fixture closure (not just callspec params) so a plain `def test(adapter_id)` — no
    # decorator, no parametrize — is caught here rather than only at fixture setup.
    if "adapter_id" in fixtures and not each:
        return "uses `adapter_id` without @per_adapter — use the decorator, not a hand-rolled matrix"
    # A topology decorator that injects no agent/agents/cell provisions nothing — almost
    # certainly a mistake (the decorator gates + parametrizes but the test uses no agent).
    if (each or group) and not (wants_agent or wants_agents or wants_cell):
        return "declares @per_adapter/@with_adapters but requests none of agent/agents/cell"
    return None


def assert_agent_fixtures_wired(items: list[pytest.Item]) -> None:
    """Fail collection for any test whose agent-fixture wiring is invalid.

    Runs in every collection (lane-scoped or not) so a mis-wired test fails before it
    reaches CI — and, since it runs at collection, before any agent is provisioned.
    """
    offenders = [
        f"{item.nodeid}: {reason}"
        for item in items
        if (reason := _wiring_error(item)) is not None
    ]
    if offenders:
        joined = "\n  ".join(offenders)
        raise pytest.UsageError(
            "agent-fixture wiring error(s) — fix the decorator/fixture pairing "
            f"(see agents.py / fixtures/agents.py):\n  {joined}"
        )
