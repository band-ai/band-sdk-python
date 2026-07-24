"""Configuration shared by the OpenCode examples."""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class OpenCodeExampleSettings(BaseSettings):
    """Read and validate the environment used by the OpenCode examples."""

    band_ws_url: str
    band_rest_url: str
    agent_key: str = "darter"
    opencode_base_url: str = "http://127.0.0.1:4096"
    opencode_provider_id: str = "opencode"
    opencode_model_id: str = "minimax-m2.5-free"
    opencode_agent: str | None = None
    opencode_directory: str | None = None
    opencode_workspace: str | None = None
    opencode_approval_mode: Literal["manual", "auto_accept", "auto_decline"] = "manual"

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,
    )
