"""ACP retention requires provider-specific whole-session safety."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from band.adapters.antigravity_acp import AntigravityACPAdapterConfig
from band.adapters.copilot_acp import CopilotACPAdapterConfig
from band.adapters.cursor_acp import CursorACPAdapterConfig
from band.adapters.omp_acp import OmpACPAdapterConfig
from band.core.retention import RoomRetentionConfig
from band.integrations.acp.client_adapter import ACPClientAdapterConfig

RETENTION_PROFILES = [
    (ACPClientAdapterConfig, "whole-session inactivity"),
    (AntigravityACPAdapterConfig, "not qualified for safe idle suspension and resume"),
    (CopilotACPAdapterConfig, "scheduled prompts"),
    (CursorACPAdapterConfig, "subagent drainage"),
    (OmpACPAdapterConfig, "suppressed or acknowledged background jobs"),
]


class TestACPRetentionConfig:
    @pytest.mark.parametrize(("config_type", "reason"), RETENTION_PROFILES)
    def test_profiles_reject_enabled_retention_in_python(
        self, config_type: type[ACPClientAdapterConfig], reason: str
    ) -> None:
        with pytest.raises(ValidationError, match=reason):
            config_type(
                command=("provider",), retention=RoomRetentionConfig(idle_timeout_s=1)
            )

    @pytest.mark.parametrize(("config_type", "reason"), RETENTION_PROFILES)
    def test_profiles_reject_enabled_retention_in_json(
        self, config_type: type[ACPClientAdapterConfig], reason: str
    ) -> None:
        with pytest.raises(ValidationError, match=reason):
            config_type.model_validate_json(
                '{"command": ["provider"], "retention": {"idle_timeout_s": 1}}'
            )

    def test_configuration_cannot_override_qualification_reason(self) -> None:
        with pytest.raises(ValidationError, match="retention_unavailable_reason"):
            ACPClientAdapterConfig.model_validate(
                {"command": ["provider"], "retention_unavailable_reason": "qualified"}
            )
