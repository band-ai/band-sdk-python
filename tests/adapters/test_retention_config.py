"""Reject unsafe retention before provider startup."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from band.adapters.claude_sdk import ClaudeSDKAdapterConfig
from band.adapters.codex import CodexAdapterConfig
from band.core.retention import RoomRetentionConfig


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("-inf"), float("nan")])
def test_retention_rejects_invalid_deadlines(timeout: float) -> None:
    with pytest.raises(ValidationError):
        RoomRetentionConfig(idle_timeout_s=timeout)


def test_retention_cannot_be_enabled_by_mutating_validated_config() -> None:
    retention = RoomRetentionConfig()
    with pytest.raises(ValidationError, match="frozen"):
        retention.idle_timeout_s = 1


def test_retention_rejects_misspelled_setting() -> None:
    with pytest.raises(ValidationError, match="idle_timeout_seconds"):
        RoomRetentionConfig.model_validate({"idle_timeout_seconds": 1})


@pytest.mark.parametrize(
    ("config_type", "reason"),
    [
        (CodexAdapterConfig, "asynchronous hooks and delegated work"),
        (ClaudeSDKAdapterConfig, "asynchronous managed hooks"),
    ],
)
@pytest.mark.parametrize("serialized", [False, True], ids=["python", "json"])
def test_native_profiles_reject_enabled_retention(
    config_type: type[CodexAdapterConfig | ClaudeSDKAdapterConfig],
    reason: str,
    serialized: bool,
) -> None:
    with pytest.raises(ValidationError, match=reason):
        if serialized:
            config_type.model_validate_json('{"retention": {"idle_timeout_s": 1}}')
        else:
            config_type(retention=RoomRetentionConfig(idle_timeout_s=1))


def test_codex_environment_cannot_bypass_retention_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODEX_RETENTION", '{"idle_timeout_s": 1}')
    with pytest.raises(ValidationError, match="asynchronous hooks"):
        CodexAdapterConfig()


@pytest.mark.parametrize("config_type", [CodexAdapterConfig, ClaudeSDKAdapterConfig])
def test_nested_retention_rejects_misspelled_setting(
    config_type: type[CodexAdapterConfig | ClaudeSDKAdapterConfig],
) -> None:
    with pytest.raises(ValidationError, match="retention.idle_timeout_seconds"):
        config_type.model_validate({"retention": {"idle_timeout_seconds": 1}})
