"""Read host environment configuration without importing it in isolated callbacks."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class RunnerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    path: str = os.defpath
    contract_qualification_process_log: Path | None = None
    openai_api_key: SecretStr = SecretStr("")


def runner_settings() -> RunnerSettings:
    try:
        return RunnerSettings()
    except ValidationError:
        raise ValueError("Invalid contract runner environment configuration") from None
