"""Codex adapter: configuration defaults and rejected settings."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from band.adapters.codex import CodexAdapterConfig


class TestAdapterConfig:
    def test_config_defaults_are_low_noise_and_manual_approval(
        self, assert_no_leaked_adapter_config_env: None
    ) -> None:
        config = CodexAdapterConfig()
        assert config.emit_turn_task_markers is False
        assert config.approval_mode == "manual"

    @pytest.mark.parametrize(
        "field", ["fallback_send_agent_text", "assistant_text_mode"]
    )
    def test_retired_delivery_config_is_rejected(self, field: str) -> None:
        with pytest.raises(ValidationError, match="Extra inputs"):
            CodexAdapterConfig.model_validate({field: True})

    def test_a_negative_settle_timeout_is_rejected(self) -> None:
        """A negative grace would expire before a leftover native request is
        answered."""
        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            CodexAdapterConfig(turn_settle_timeout_s=-1)
