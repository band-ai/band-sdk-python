from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from band.adapters.crewai_flow import (
    CrewAIFlowAdapterConfig,
)


class TestConfigValidation:
    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("join_policy", "some_other", "'all' or 'first'"),
            ("text_only_behavior", "bogus", "'error_event' or 'fallback_send'"),
            ("tagged_peer_policy", "something_else", "'off'"),
            ("metadata_namespace", "", "at least 1 character"),
            ("max_delegation_rounds", 0, "greater than or equal to 1"),
            ("max_delegation_rounds", 21, "less than or equal to 20"),
            ("max_delegation_rounds", True, "valid integer"),
            ("max_run_age", timedelta(0), "max_run_age must be positive"),
            ("max_run_age", timedelta(seconds=-1), "max_run_age must be positive"),
            ("sequential_chains", {"k": 123}, "valid string"),
            ("accept_agent_initiated", "yes", "valid boolean"),
        ],
    )
    def test_rejects_invalid_policy(self, field: str, value: Any, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            CrewAIFlowAdapterConfig.model_validate({field: value})

    def test_max_run_age_accepts_seconds_from_plain_data(self) -> None:
        config = CrewAIFlowAdapterConfig.model_validate({"max_run_age": 86400})
        assert config.max_run_age == timedelta(days=1)
