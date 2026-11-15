# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[acp]>=7.0.0"]
# [tool.uv.sources]
# band-sdk = { path = "../../.." }
# ///
"""Share explicit credentials and native settings across Antigravity examples."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from band.adapters.antigravity_acp import AntigravityACPAdapterConfig
from band.core.types import ApprovalMode

DEFAULT_CONFIG = AntigravityACPAdapterConfig()
MISSING_PROVIDER_KEY = "Set GEMINI_API_KEY or GOOGLE_API_KEY in .env.test"


class Settings(BaseSettings):
    """Read example overrides while retaining the SDK's native defaults."""

    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    gemini_api_key: str = Field(default="", repr=False)
    google_api_key: str = Field(default="", repr=False)
    antigravity_command: tuple[str, ...] = DEFAULT_CONFIG.command
    acp_agent_cwd: str | None = DEFAULT_CONFIG.cwd
    antigravity_home: str = DEFAULT_CONFIG.gemini_home
    antigravity_model: str | None = DEFAULT_CONFIG.model
    antigravity_reasoning_effort: str | None = DEFAULT_CONFIG.reasoning_effort
    antigravity_approval_mode: ApprovalMode = DEFAULT_CONFIG.approval_mode


def build_config(
    settings: Settings, *, custom_section: str = DEFAULT_CONFIG.custom_section
) -> AntigravityACPAdapterConfig:
    """Validate provider credentials and map the shared native settings."""
    api_key = settings.gemini_api_key or settings.google_api_key
    if not api_key:
        raise ValueError(MISSING_PROVIDER_KEY)
    return AntigravityACPAdapterConfig(
        command=settings.antigravity_command,
        cwd=settings.acp_agent_cwd,
        gemini_api_key=api_key,
        gemini_home=settings.antigravity_home,
        model=settings.antigravity_model,
        reasoning_effort=settings.antigravity_reasoning_effort,
        approval_mode=settings.antigravity_approval_mode,
        custom_section=custom_section,
    )
