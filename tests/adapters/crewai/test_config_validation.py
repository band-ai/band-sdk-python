from __future__ import annotations

import pytest

from band.adapters.crewai import (
    CrewAIAdapterConfig,
)


class TestConfigValidation:
    @pytest.mark.parametrize("field", ["max_iter", "max_rpm"])
    @pytest.mark.parametrize("value", [0, -1])
    def test_iteration_and_rate_limits_must_be_positive(
        self, field: str, value: int
    ) -> None:
        with pytest.raises(ValueError, match="greater than 0"):
            CrewAIAdapterConfig.model_validate({field: value})
