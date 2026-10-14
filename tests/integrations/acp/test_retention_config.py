"""ACP retention requires provider-specific whole-session safety."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from band.adapters.copilot_acp import CopilotACPAdapterConfig
from band.adapters.cursor_acp import CursorACPAdapterConfig
from band.adapters.omp_acp import OmpACPAdapterConfig
from band.core.retention import RoomRetentionConfig
from band.integrations.acp.client_adapter import ACPClientAdapterConfig


@pytest.mark.parametrize(
    ("config_type", "reason"),
    [
        (ACPClientAdapterConfig, "whole-session inactivity"),
        (CopilotACPAdapterConfig, "scheduled prompts"),
        (CursorACPAdapterConfig, "subagent drainage"),
        (OmpACPAdapterConfig, "suppressed or acknowledged background jobs"),
    ],
)
@pytest.mark.parametrize("serialized", [False, True], ids=["python", "json"])
def test_acp_profiles_reject_enabled_retention(
    config_type: type[ACPClientAdapterConfig], reason: str, serialized: bool
) -> None:
    with pytest.raises(ValidationError, match=reason):
        if serialized:
            config_type.model_validate_json(
                '{"command": ["provider"], "retention": {"idle_timeout_s": 1}}'
            )
        else:
            config_type(
                command=("provider",), retention=RoomRetentionConfig(idle_timeout_s=1)
            )


def test_acp_configuration_cannot_override_qualification_reason() -> None:
    with pytest.raises(ValidationError, match="retention_unavailable_reason"):
        ACPClientAdapterConfig.model_validate(
            {"command": ["provider"], "retention_unavailable_reason": "qualified"}
        )
