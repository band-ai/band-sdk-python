"""Configuration shared by the OpenCode examples."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from band.adapters.opencode import ApprovalMode

# Anchored to the repository root so every example reads the same `.env`
# whatever the working directory, like the other examples' `load_dotenv()`.
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class OpenCodeExampleSettings(BaseSettings):
    """Read and validate the environment used by the OpenCode examples."""

    agent_key: str = "darter"
    opencode_base_url: str = "http://127.0.0.1:4096"
    opencode_provider_id: str = "opencode"
    opencode_model_id: str = "mimo-v2.5-free"
    opencode_agent: str | None = None
    opencode_directory: str | None = None
    opencode_workspace: str | None = None
    opencode_approval_mode: ApprovalMode = "manual"

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,
    )
