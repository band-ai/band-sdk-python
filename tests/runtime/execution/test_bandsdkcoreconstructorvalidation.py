"""BandSdkCoreConstructorValidation behavior."""

from __future__ import annotations

import pytest
from band_sdk_core import ClaimRegistry, RetryTracker


class TestBandSdkCoreConstructorValidation:
    """Regression guard for band-sdk-core's RetryTracker.max_retries
    range-validation gap, fixed in 0.7.2: every zero-capacity/out-of-range
    constructor argument must raise a clean ValueError, never a bare
    OverflowError. Runs against the actual installed band_sdk_core artifact,
    not a mock -- ExecutionContext constructs both types directly from it."""

    @pytest.mark.parametrize(
        "factory",
        [
            pytest.param(
                lambda: ClaimRegistry(max_completed=0), id="claim-zero-capacity"
            ),
            pytest.param(
                lambda: RetryTracker(max_tracked=0), id="retry-zero-max-tracked"
            ),
            pytest.param(
                lambda: RetryTracker(max_retries=-1), id="retry-negative-max-retries"
            ),
            pytest.param(
                lambda: RetryTracker(max_retries=4294967296),  # u32::MAX + 1
                id="retry-max-retries-overflows-u32",
            ),
        ],
    )
    def test_rejects_invalid_constructor_args(self, factory):
        with pytest.raises(ValueError):
            factory()
