"""Adapter × delivery-scenario matrix — tool-first reply suppression."""

from __future__ import annotations

import pytest

from tests.baseline.delivery.runners import run_delivery
from tests.baseline.delivery.scenarios import (
    DELIVERY_ADAPTERS,
    SCENARIOS,
    DeliveryScenario,
)


def _copilot_marks(adapter: str) -> tuple[pytest.MarkDecorator, ...]:
    if adapter != "copilot_sdk":
        return ()
    from band.adapters.copilot_sdk import _COPILOT_SDK_AVAILABLE

    if _COPILOT_SDK_AVAILABLE:
        return ()
    return (
        pytest.mark.skip(
            reason="github-copilot-sdk not installed "
            "(pip install band-sdk[copilot_sdk])"
        ),
    )


def _matrix_params() -> list[pytest.ParameterSet]:
    """Expand delivery contracts into the adapter test matrix."""
    pool = frozenset(DELIVERY_ADAPTERS)
    return [
        pytest.param(
            adapter,
            scenario,
            id=f"{adapter}:{scenario.id}",
            marks=_copilot_marks(adapter),
        )
        for scenario in SCENARIOS
        for adapter in scenario.covers(pool)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(("adapter", "scenario"), _matrix_params())
async def test_delivery_shape_matches_scenario(
    adapter: str,
    scenario: DeliveryScenario,
) -> None:
    """Each bridge closes the turn with the scenario's delivery shape."""
    outcome = await run_delivery(adapter, scenario)
    outcome.assert_matches(scenario)
