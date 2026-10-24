"""Fresh context precedes admission; old context cannot override fresh discovery."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from tests.runtime.delivery.kit import write
from tests.runtime.scheduler.hydration import (
    CONTROL_EFFECT,
    ORDINARY_EFFECT,
    observe_handoff,
)

PROCESSING, PROCESSED, FAILED = (
    DeliveryStatus.PROCESSING,
    DeliveryStatus.PROCESSED,
    DeliveryStatus.FAILED,
)
TARGET_WRITES = (write(PROCESSING, "target"), write(PROCESSED, "target"))
ORDINARY_EFFECTS = (ORDINARY_EFFECT,)
CONTROL_EFFECTS = (CONTROL_EFFECT,)

# Only a cold, ordinary original whose history shows it processed is dropped.
ORDINARY_OUTCOMES = [
    pytest.param(
        False, PROCESSING, ORDINARY_EFFECTS, TARGET_WRITES, id="new-processing"
    ),
    pytest.param(False, PROCESSED, (), (), id="new-processed"),
    pytest.param(False, FAILED, ORDINARY_EFFECTS, TARGET_WRITES, id="new-failed"),
    pytest.param(
        True, PROCESSING, ORDINARY_EFFECTS, TARGET_WRITES, id="old-processing"
    ),
    pytest.param(True, PROCESSED, ORDINARY_EFFECTS, TARGET_WRITES, id="old-processed"),
    pytest.param(True, FAILED, ORDINARY_EFFECTS, TARGET_WRITES, id="old-failed"),
]


class TestHydrationHandoff:
    @pytest.mark.parametrize(
        ("warm", "history", "effects", "target_writes"), ORDINARY_OUTCOMES
    )
    @pytest.mark.parametrize("stale", [FAILED, PROCESSED])
    async def test_ordinary_context_read_order_and_admitted_membership(
        self,
        warm: bool,
        history: DeliveryStatus,
        stale: DeliveryStatus,
        effects: tuple[str, ...],
        target_writes: tuple[str, ...],
    ) -> None:
        observed = await observe_handoff(
            batching=False, control=False, warm=warm, history=history, stale=stale
        )
        assert observed.effects == effects
        assert observed.original_preserved
        assert observed.target_writes == target_writes

    @pytest.mark.parametrize("warm", [False, True], ids=["new", "old"])
    @pytest.mark.parametrize("history", [PROCESSING, PROCESSED, FAILED])
    @pytest.mark.parametrize("stale", [FAILED, PROCESSED])
    async def test_control_handoff_preserves_the_refused_head(
        self, warm: bool, history: DeliveryStatus, stale: DeliveryStatus
    ) -> None:
        observed = await observe_handoff(
            batching=False, control=True, warm=warm, history=history, stale=stale
        )
        assert observed.effects == CONTROL_EFFECTS
        assert observed.original_preserved
        assert observed.target_writes == TARGET_WRITES
        assert observed.blocked_claimed
        assert observed.blocked_status is not PROCESSED

    @pytest.mark.parametrize("batching", [False])
    @pytest.mark.parametrize(
        ("control", "effects"),
        [
            pytest.param(False, ORDINARY_EFFECTS, id="ordinary"),
            pytest.param(True, CONTROL_EFFECTS, id="priority"),
        ],
    )
    @pytest.mark.parametrize("warm", [False, True], ids=["new-context", "old-cache"])
    async def test_fresh_failed_discovery_obeys_the_configured_replay_policy(
        self, batching: bool, control: bool, warm: bool, effects: tuple[str, ...]
    ) -> None:
        observed = await observe_handoff(
            batching=batching,
            control=control,
            warm=warm,
            history=DeliveryStatus.PROCESSING,
            stale=DeliveryStatus.PROCESSING,
            discovery=DeliveryStatus.FAILED,
        )
        assert observed.effects == effects
        assert observed.original_preserved
        assert observed.target_writes == TARGET_WRITES
