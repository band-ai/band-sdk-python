#!/usr/bin/env python3
"""Create test agents for E2E testing.

Registers planner and reviewer agents via User API
and writes their credentials to YAML config files.

Usage:
    BAND_API_KEY=band_u_... python create_agents.py
"""

from __future__ import annotations

import asyncio
import logging
import os

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

from band import LoggingStyle, LogSettings
from band_rest import AsyncRestClient
from band_rest.types import AgentRegisterRequest

# The bare message format only exists for the standard style, so the style is
# pinned rather than read from BAND_LOG_CONSOLE_STYLE.
LogSettings(log_console_style=LoggingStyle.STANDARD).for_application().configure(
    fmt="%(message)s"
)
logger = logging.getLogger(__name__)

AGENTS = [
    {"name": "Planner", "role": "planner", "file": "planner.yaml"},
    {"name": "Reviewer", "role": "reviewer", "file": "reviewer.yaml"},
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    band_api_key: str
    band_rest_url: str = "https://app.band.ai"


async def main() -> None:
    settings = Settings()

    client = AsyncRestClient(
        api_key=settings.band_api_key, base_url=settings.band_rest_url
    )

    created = []
    script_dir = os.path.dirname(os.path.abspath(__file__))

    for agent_def in AGENTS:
        logger.info("Creating agent: %s ...", agent_def["name"])
        response = await client.human_api_agents.register_my_agent(
            agent=AgentRegisterRequest(
                name=agent_def["name"],
                description=f"E2E test agent - {agent_def['role']} role",
            )
        )

        agent = response.data.agent
        credentials = response.data.credentials

        logger.info("  Created: %s (ID: %s)", agent.name, agent.id)

        # Omit `model` so the runner falls back to None and the npm `claude`
        # binary picks its own default.  Override per agent by adding
        # `"model": "opus"` (or any alias / pinned ID) below.
        config = {
            "agent_id": agent.id,
            "api_key": credentials.api_key,
            "role": agent_def["role"],
        }

        config_path = os.path.join(script_dir, agent_def["file"])
        with open(config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False)
        logger.info("  Config written to: %s", agent_def["file"])

        created.append(
            {
                "name": agent.name,
                "id": agent.id,
                "api_key": credentials.api_key,
                "role": agent_def["role"],
                "file": agent_def["file"],
            }
        )

    logger.info("\n=== Summary ===")
    for a in created:
        logger.info(
            "%s: id=%s, role=%s, config=%s", a["name"], a["id"], a["role"], a["file"]
        )

    # Write agent IDs to a cleanup file for later deletion
    cleanup_path = os.path.join(script_dir, ".agent_ids.txt")
    with open(cleanup_path, "w") as f:
        for a in created:
            f.write(f"{a['id']}\n")
    logger.info("\nAgent IDs saved to .agent_ids.txt for cleanup")


if __name__ == "__main__":
    asyncio.run(main())
