# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[acp]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""Run Oh My P.I.'s ACP server as a Band agent.

Install Bun (>=1.3.14) and OMP first:

    npm install -g @oh-my-pi/pi-coding-agent

Then set ``OPENAI_API_KEY``, configure the Band agent in ``agent_config.yaml``, and
run this file. OMP receives the provider key only as the spawned process environment.
Do not use ``--yolo`` or auto-approve modes: Band's permission resolver must be able
to cancel unsafe calls.
"""

from __future__ import annotations

import asyncio
import logging
import shlex

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

from band import Agent, configure_logging
from band.adapters import OmpACPAdapter, OmpACPAdapterConfig
from band.config import load_agent_config
from band.integrations.omp import (
    DEFAULT_OMP_MODEL,
    OMP_ACP_SUBCOMMAND,
    OMP_ALWAYS_ASK_APPROVAL_MODE,
    OMP_APPROVAL_MODE_ARGUMENT,
    OMP_BINARY,
    OMP_MODEL_ARGUMENT,
    OMP_OPENAI_API_KEY_ENV,
)

configure_logging(level=logging.INFO, root_level=logging.INFO)
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Configuration for the local OMP ACP process."""

    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    omp_command: str = OMP_BINARY
    omp_cwd: str = "."
    omp_model: str = DEFAULT_OMP_MODEL
    openai_api_key: str = ""

    @property
    def api_key(self) -> str:
        """Return the OpenAI credential OMP uses for its configured model."""
        if not self.openai_api_key:
            raise ValueError(f"Set {OMP_OPENAI_API_KEY_ENV} before starting OMP")
        return self.openai_api_key

    @property
    def command(self) -> tuple[str, ...]:
        """Return OMP's explicit non-yolo ACP command."""
        return (
            *(shlex.split(self.omp_command) or [OMP_BINARY]),
            OMP_ACP_SUBCOMMAND,
            OMP_MODEL_ARGUMENT,
            self.omp_model,
            OMP_APPROVAL_MODE_ARGUMENT,
            OMP_ALWAYS_ASK_APPROVAL_MODE,
        )


async def main() -> None:
    load_dotenv()
    settings = Settings()
    agent_id, api_key = load_agent_config("acp_client_agent")
    adapter = OmpACPAdapter(
        OmpACPAdapterConfig(
            command=settings.command,
            cwd=settings.omp_cwd,
            env={OMP_OPENAI_API_KEY_ENV: settings.api_key},
        )
    )

    logger.info("Starting OMP ACP bridge with command: %s", " ".join(settings.command))
    async with Agent.create(
        adapter=adapter, agent_id=agent_id, api_key=api_key
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
