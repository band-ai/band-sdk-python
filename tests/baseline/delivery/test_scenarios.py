"""Self-check: scenario table semantics hold on ObservingTools + FakeAgentTools."""

from __future__ import annotations

import pytest

from tests.baseline.delivery.checks import run_scenario_on_observing_tools
from tests.baseline.delivery.scenarios import SCENARIOS, DeliveryScenario


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    [pytest.param(s, id=s.id) for s in SCENARIOS],
)
async def test_scenario_semantics_on_observing_tools(
    scenario: DeliveryScenario,
) -> None:
    """Each row's delivery shape holds without an adapter."""
    outcome = await run_scenario_on_observing_tools(scenario)
    outcome.assert_matches(scenario)
