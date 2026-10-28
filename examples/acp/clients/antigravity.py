# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[acp]>=7.0.0"]
# [tool.uv.sources]
# band-sdk = { path = "../../.." }
# ///
"""Connect Band rooms to Google's standalone Antigravity ACP server.

Run from this repository checkout. The inline source override installs this
source tree rather than a published wheel. Setup is in examples/acp/README.md.
"""

from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from band import Agent, configure_logging
from band.adapters import AntigravityACPAdapter, AntigravityACPAdapterConfig
from band.core.types import ApprovalMode

logger = logging.getLogger(__name__)
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


def build_adapter(settings: Settings) -> AntigravityACPAdapter:
    """Build the room adapter with an explicitly supplied provider credential."""
    api_key = settings.gemini_api_key or settings.google_api_key
    if not api_key:
        raise ValueError(MISSING_PROVIDER_KEY)

    return AntigravityACPAdapter(
        AntigravityACPAdapterConfig(
            command=settings.antigravity_command,
            cwd=settings.acp_agent_cwd,
            gemini_api_key=api_key,
            gemini_home=settings.antigravity_home,
            model=settings.antigravity_model,
            reasoning_effort=settings.antigravity_reasoning_effort,
            approval_mode=settings.antigravity_approval_mode,
        )
    )


async def main() -> None:
    """Run the configured Antigravity agent until interrupted."""
    load_dotenv(".env.test")
    settings = Settings()
    adapter = build_adapter(settings)
    configure_logging()

    logger.info("Starting the Antigravity ACP bridge to Band rooms")
    async with Agent.from_config("antigravity_agent", adapter=adapter) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
