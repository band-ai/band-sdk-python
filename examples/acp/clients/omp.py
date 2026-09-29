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
from band.integrations.omp import (
    DEFAULT_OMP_MODEL,
    omp_model_provider,
    omp_provider_api_key_env,
    omp_provider_env,
)

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
    # omp_provider_api_key_env is the SDK's single source of truth for which
    # providers OMP supports; this example only wires up credentials for the
    # three most common ones, so an OMP-supported-but-unwired provider gets an
    # actionable message instead of being misreported as unsupported by OMP.
    env_key = omp_provider_api_key_env(omp_model_provider(settings.omp_model))
    match env_key:
        case "GEMINI_API_KEY":
            return settings.gemini_api_key
        case "ANTHROPIC_API_KEY":
            return settings.anthropic_api_key
        case "OPENAI_API_KEY":
            return settings.openai_api_key
        case _:
            raise ValueError(
                f"{settings.omp_model!r} needs {env_key}, which this example "
                "doesn't read; add a field for it to Settings, or use "
                "anthropic/google/openai"
            )


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
