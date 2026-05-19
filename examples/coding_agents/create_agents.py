#!/usr/bin/env python3
"""Create test agents for E2E testing.

Registers planner and reviewer agents via User API
and writes their credentials to YAML config files.

Usage:
    THENVOI_API_KEY=thnv_u_... python create_agents.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import yaml

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# Add repo root to path for thenvoi_rest import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

AGENTS = [
    {"name": "Planner", "role": "planner", "file": "planner.yaml"},
    {"name": "Reviewer", "role": "reviewer", "file": "reviewer.yaml"},
]


async def main() -> None:
    api_key = os.environ.get("THENVOI_API_KEY")
    if not api_key:
        raise ValueError("THENVOI_API_KEY environment variable is required")

    base_url = os.environ.get("THENVOI_REST_URL", "https://app.thenvoi.com")

    from thenvoi_rest import AsyncRestClient
    from thenvoi_rest.types import AgentRegisterRequest

    client = AsyncRestClient(api_key=api_key, base_url=base_url)

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
