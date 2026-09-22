# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[acp]>=1.2.0"]
# ///
"""
OMP ACP client — bridge Band rooms to ``omp acp``.

Spawns OMP's native ACP stdio server with ``always-ask`` approval mode enforced
by the SDK, injects Band tools over loopback MCP, and forwards room messages.
Provider credentials are passed only to the OMP child process environment.
"""

from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

from band import Agent, configure_logging
from band.adapters import OmpACPAdapter, OmpACPAdapterConfig
from band.integrations.omp import DEFAULT_OMP_MODEL, omp_provider_env

configure_logging(level=logging.INFO, root_level=logging.INFO)
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    acp_agent_cwd: str = "."
    omp_model: str = DEFAULT_OMP_MODEL
    gemini_api_key: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""


def _provider_api_key(settings: Settings) -> str:
    provider = settings.omp_model.split("/", 1)[0].lower()
    match provider:
        case "google":
            return settings.gemini_api_key
        case "anthropic":
            return settings.anthropic_api_key
        case "openai":
            return settings.openai_api_key
        case _:
            raise ValueError(f"Unsupported OMP model provider in {settings.omp_model}")


async def main() -> None:
    load_dotenv()
    settings = Settings()
    api_key = _provider_api_key(settings)
    if not api_key:
        raise ValueError("Set the provider API key env var matching OMP_MODEL")

    config = OmpACPAdapterConfig(
        cwd=settings.acp_agent_cwd,
        env=omp_provider_env(model=settings.omp_model, api_key=api_key),
        inject_band_tools=True,
    )
    adapter = OmpACPAdapter(config)

    logger.info("Starting OMP ACP client bridge...")
    async with Agent.from_config("omp_acp_agent", adapter=adapter) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
