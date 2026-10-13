"""An operation whose room keeps changing under it stops at its step budget."""

from __future__ import annotations

import pytest

from band.core.retention import room as room_module
from tests.core.retention.kit import ResidenceHost

pytestmark = pytest.mark.timeout(10)


class TestStepBudget:
    # Correct transitions stay within both budgets, so each test narrows one
    # to make an ordinary transition sequence exceed it.

    async def test_becoming_resident_beyond_its_budget_fails_loudly(
        self, host: ResidenceHost, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(room_module, "MAX_RESIDENCY_STEPS", 1)
        resident = host.room()

        with pytest.raises(RuntimeError, match="did not settle within 1 steps"):
            await resident.turn()

    async def test_settling_beyond_its_budget_fails_loudly(
        self, host: ResidenceHost, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resident = host.room()
        await resident.turn()
        resident.fail_close = True
        await host.clock.advance(10)
        await resident.suspended.get()
        resident.fail_close = False
        monkeypatch.setattr(room_module, "MAX_SETTLE_STEPS", 1)

        with pytest.raises(RuntimeError, match="did not settle within 1 steps"):
            await resident.turn()
