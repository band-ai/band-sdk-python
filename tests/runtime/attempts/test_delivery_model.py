"""Generated sequences preserve the same delivery contract across replacement."""

from __future__ import annotations

from hypothesis import settings
from hypothesis.stateful import run_state_machine_as_test

from tests.runtime.attempts.model import DeliveryModel


class TestDeliveryModel:
    def test_execution_and_settlement_are_distinct_lifecycles(self) -> None:
        run_state_machine_as_test(
            DeliveryModel,
            settings=settings(
                max_examples=75,
                stateful_step_count=30,
                deadline=None,
                database=None,
                derandomize=True,
            ),
        )
