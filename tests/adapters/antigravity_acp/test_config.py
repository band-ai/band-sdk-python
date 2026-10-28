"""Reject contradictory Antigravity configuration before starting a process."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from band.adapters.antigravity_acp import AntigravityACPAdapterConfig


class TestAntigravityConfig:
    @pytest.mark.parametrize("key", ["GEMINI_HOME", "gemini_home", "Gemini_Home"])
    def test_home_environment_override_names_the_owned_setting(self, key: str) -> None:
        with pytest.raises(ValidationError, match="gemini_home"):
            AntigravityACPAdapterConfig(env={key: "shared-home"})

    def test_manual_approval_is_not_advertised(self) -> None:
        with pytest.raises(ValidationError, match="approval_mode"):
            AntigravityACPAdapterConfig(approval_mode="manual")

    def test_empty_home_is_not_the_current_directory(self) -> None:
        with pytest.raises(ValidationError, match="gemini_home"):
            AntigravityACPAdapterConfig(gemini_home="")

    def test_api_key_is_not_printed_in_config(self) -> None:
        secret = "gemini-test-secret"
        assert secret not in repr(AntigravityACPAdapterConfig(gemini_api_key=secret))
