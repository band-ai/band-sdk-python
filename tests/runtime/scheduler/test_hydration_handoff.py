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

# Cold history informs admission; warm history cannot override fresh discovery.
ORDINARY_OUTCOMES = [
    pytest.param(
        False,
        False,
        PROCESSING,
        ORDINARY_EFFECTS,
        TARGET_WRITES,
        id="singleton-new-processing",
    ),
    pytest.param(False, False, PROCESSED, (), (), id="singleton-new-processed"),
    pytest.param(
        False, False, FAILED, ORDINARY_EFFECTS, TARGET_WRITES, id="singleton-new-failed"
    ),
    pytest.param(
        False,
        True,
        PROCESSING,
        ORDINARY_EFFECTS,
        TARGET_WRITES,
        id="singleton-old-processing",
    ),
    pytest.param(
        False,
        True,
        PROCESSED,
        ORDINARY_EFFECTS,
        TARGET_WRITES,
        id="singleton-old-processed",
    ),
    pytest.param(
        False, True, FAILED, ORDINARY_EFFECTS, TARGET_WRITES, id="singleton-old-failed"
    ),
    pytest.param(
        True,
        False,
        PROCESSING,
        ORDINARY_EFFECTS,
        TARGET_WRITES,
        id="batch-new-processing",
    ),
    pytest.param(True, False, PROCESSED, (), (), id="batch-new-processed"),
    pytest.param(True, False, FAILED, (), (), id="batch-new-failed"),
    pytest.param(
        True,
        True,
        PROCESSING,
        ORDINARY_EFFECTS,
        TARGET_WRITES,
        id="batch-old-processing",
    ),
    pytest.param(
        True, True, PROCESSED, ORDINARY_EFFECTS, TARGET_WRITES, id="batch-old-processed"
    ),
    pytest.param(
        True, True, FAILED, ORDINARY_EFFECTS, TARGET_WRITES, id="batch-old-failed"
    ),
]
FAILED_DISCOVERY_OUTCOMES = [
    pytest.param(
        False, False, ORDINARY_EFFECTS, TARGET_WRITES, id="singleton-ordinary"
    ),
    pytest.param(False, True, CONTROL_EFFECTS, TARGET_WRITES, id="singleton-priority"),
    pytest.param(True, False, (), (), id="batch-ordinary"),
    pytest.param(True, True, (), (), id="batch-priority"),
]


class TestHydrationHandoff:
    @pytest.mark.parametrize(
        ("batching", "warm", "history", "effects", "target_writes"), ORDINARY_OUTCOMES
    )
    @pytest.mark.parametrize("stale", [FAILED, PROCESSED])
    async def test_ordinary_context_read_order_and_admitted_membership(
        self,
        batching: bool,
        warm: bool,
        history: DeliveryStatus,
        stale: DeliveryStatus,
        effects: tuple[str, ...],
        target_writes: tuple[str, ...],
    ) -> None:
        observed = await observe_handoff(
            batching=batching, control=False, warm=warm, history=history, stale=stale
        )
        assert observed.effects == effects
        assert observed.original_preserved
        assert observed.target_writes == target_writes

    @pytest.mark.parametrize("batching", [False, True], ids=["singleton", "batch"])
    @pytest.mark.parametrize("warm", [False, True], ids=["new", "old"])
    @pytest.mark.parametrize("history", [PROCESSING, PROCESSED, FAILED])
    @pytest.mark.parametrize("stale", [FAILED, PROCESSED])
    async def test_control_handoff_preserves_the_refused_head(
        self, batching: bool, warm: bool, history: DeliveryStatus, stale: DeliveryStatus
    ) -> None:
        observed = await observe_handoff(
            batching=batching, control=True, warm=warm, history=history, stale=stale
        )
        assert observed.effects == CONTROL_EFFECTS
        assert observed.original_preserved
        assert observed.target_writes == TARGET_WRITES
        assert observed.blocked_claimed
        assert observed.blocked_status is not PROCESSED

    @pytest.mark.parametrize(
        ("batching", "control", "effects", "target_writes"), FAILED_DISCOVERY_OUTCOMES
    )
    @pytest.mark.parametrize("warm", [False, True], ids=["new-context", "old-cache"])
    async def test_fresh_failed_discovery_obeys_the_configured_replay_policy(
        self,
        batching: bool,
        control: bool,
        warm: bool,
        effects: tuple[str, ...],
        target_writes: tuple[str, ...],
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
        assert observed.target_writes == target_writes
