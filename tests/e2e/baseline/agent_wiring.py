"""Collection-time guard for the agent-fixture / decorator pairing.

Fails collection (before any live agent is provisioned) when a test wires the agent
fixtures in a way that can't work or that bypasses the one-vocabulary rule:

* a topology decorator is applied **at most once** (no stacked ``@per_adapter`` /
  ``@with_adapters`` — the fixtures read the closest marker, so a second one would be
  silently dropped);
* ``cell`` needs ``@per_adapter``; ``agent`` / ``agents`` need one of the two decorators;
* ``agents`` is group-only, ``cell`` is fan-only;
* a test picks exactly one topology (not both decorators) and one provisioning mode
  (not both ``agent`` and ``cell``);
* ``peer`` needs ``@per_adapter(peer=...)`` and vice versa;
* nobody hand-rolls a matrix by parametrizing ``adapter_id`` without ``@per_adapter``.

Sibling of ``lane_selection.assert_every_item_is_schedulable``; both run from the
baseline ``pytest_collection_modifyitems`` hook.
"""

from __future__ import annotations

import pytest

from tests.e2e.baseline.agents import WITH_ADAPTERS_MARKER, PER_ADAPTER_MARKER


def _wiring_error(item: pytest.Item) -> str | None:
    """The wiring violation on ``item`` (a one-line reason), or ``None`` if wired right."""
    per_marker = item.get_closest_marker(PER_ADAPTER_MARKER)
    each = per_marker is not None
    group = item.get_closest_marker(WITH_ADAPTERS_MARKER) is not None
    fixtures = set(getattr(item, "fixturenames", ()))

    def _count(marker_name: str) -> int:
        """How many times ``marker_name`` is applied — >1 means a stacked decorator that
        ``get_closest_marker`` would silently reduce to one."""
        return sum(1 for _ in item.iter_markers(marker_name))

    wants_agent = "agent" in fixtures
    wants_agents = "agents" in fixtures
    wants_cell = "cell" in fixtures
    wants_peer = "peer" in fixtures
    wants_adapter_id = "adapter_id" in fixtures
    wants_any = wants_agent or wants_agents or wants_cell
    peer_declared = each and getattr(per_marker.args[0], "peer", None) is not None

    # First matching rule wins, so order is load-bearing: the most-fundamental violation
    # comes first (a both-decorators test should report "pick one topology", not a
    # downstream fixture-pairing message). Notes on the subtler pairings:
    #  * `peer` and @per_adapter(peer=...) are two halves of one declaration — each needs
    #    the other, so a half-wired cross-framework test fails here, not at fixture setup.
    #  * `adapter_id` is @per_adapter's internal parametrize target; requesting it without
    #    the decorator is a hand-rolled matrix. Keyed on the fixture closure, so a plain
    #    `def test(adapter_id)` is caught at collection, not only at fixture setup.
    #  * a topology decorator that injects no agent/agents/cell provisions nothing.
    rules: list[tuple[bool, str]] = [
        (
            _count(PER_ADAPTER_MARKER) > 1,
            "has @per_adapter applied more than once — apply it exactly once",
        ),
        (
            _count(WITH_ADAPTERS_MARKER) > 1,
            "has @with_adapters applied more than once — apply it exactly once",
        ),
        (
            each and group,
            "has both @per_adapter and @with_adapters — pick one topology",
        ),
        (
            wants_agent and wants_cell,
            "requests both `agent` and `cell` — pick one provisioning mode",
        ),
        (
            wants_cell and group,
            "requests `cell` under @with_adapters (fan-only) — use `agent`/`agents`",
        ),
        (wants_cell and not each, "requests `cell` without @per_adapter"),
        (
            wants_agents and each,
            "requests `agents` under @per_adapter (a cell is one adapter) — use `agent`",
        ),
        (wants_peer and not each, "requests `peer` without @per_adapter(peer=...)"),
        (
            wants_peer and not peer_declared,
            "requests `peer` but @per_adapter declares no peer=",
        ),
        (
            peer_declared and not wants_peer,
            "declares @per_adapter(peer=...) but does not request the `peer` fixture",
        ),
        (
            (wants_agent or wants_agents) and not (each or group),
            "requests `agent`/`agents` with no @per_adapter / @with_adapters decorator",
        ),
        (
            wants_adapter_id and not each,
            "uses `adapter_id` without @per_adapter — use the decorator, not a hand-rolled matrix",
        ),
        (
            (each or group) and not wants_any,
            "declares @per_adapter/@with_adapters but requests none of agent/agents/cell",
        ),
    ]
    return next((message for matched, message in rules if matched), None)


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
