"""Reconciliation intervals must be finite and positive."""

from __future__ import annotations

import pytest

from band.runtime.types import SessionConfig


class TestIntervalValidation:
    """Reconciliation intervals must be finite and positive."""

    @pytest.mark.parametrize(
        "field", ["idle_resync_seconds", "idle_resync_max_seconds"]
    )
    @pytest.mark.parametrize(
        "value", [0, -1, float("nan"), float("inf"), -float("inf")]
    )
    def test_unsafe_reconciliation_intervals_are_rejected(
        self, field: str, value: float
    ) -> None:
        with pytest.raises(ValueError, match=field):
            SessionConfig(**{field: value})
