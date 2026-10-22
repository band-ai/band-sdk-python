"""Locate repository oracles with optional explicit test configuration."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from tests.paths import CONTRACTS_ROOT


class RuntimeTestSettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    band_format_ex: Path = CONTRACTS_ROOT / "contract-lab/lib/contract_lab/format.ex"
    band_contract_lab: Path = CONTRACTS_ROOT / "contract-lab"
    band_test_engine_command: list[str] | None = None


def runtime_settings() -> RuntimeTestSettings:
    return RuntimeTestSettings()
