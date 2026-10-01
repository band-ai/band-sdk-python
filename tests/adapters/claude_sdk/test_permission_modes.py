"""ClaudePermissionMode names the SDK's permission modes; it must not drift."""

from __future__ import annotations

from typing import get_args

from claude_agent_sdk.types import PermissionMode

from band.adapters.claude_sdk import ClaudePermissionMode, ClaudeSDKAdapterConfig


def test_the_enum_is_exactly_the_sdks_permission_modes() -> None:
    """A mode the SDK adds or drops fails here, on the dependency bump."""
    assert set(ClaudePermissionMode) == set(get_args(PermissionMode))


def test_a_mode_loaded_as_plain_text_becomes_the_enum() -> None:
    """Hosts load configs from YAML or JSON, where a mode is a bare string."""
    config = ClaudeSDKAdapterConfig.model_validate({"permission_mode": "dontAsk"})

    assert config.permission_mode is ClaudePermissionMode.DONT_ASK
