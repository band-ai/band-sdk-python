# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[crewai]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Mixed-example CrewAI coordinator.

Starts the CrewAI agent that steers the shared room:
- asks the contract checker bridge for concrete implementation facts
- asks the risk reviewer bridge for rollout and compatibility risks
- asks the writer for the final engineering handoff

Run with:
    uv run examples/mixed/01_strategy_coordinator.py
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from setup_logging import setup_logging
from band import Agent
from band.adapters import CrewAIAdapter
from band.config import load_agent_config
from band.core.types import AdapterFeatures, Emit

logger = logging.getLogger(__name__)
CONFIG_PATH = Path(__file__).with_name("agents.yaml")


async def main() -> None:
    setup_logging()
    load_dotenv()

    ws_url = os.getenv("THENVOI_WS_URL", "wss://app.band.ai/api/v1/socket/websocket")
    rest_url = os.getenv("THENVOI_REST_URL", "https://app.band.ai")

    agent_id, api_key = load_agent_config(
        "mixed_strategy_agent",
        config_path=CONFIG_PATH,
    )

    adapter = CrewAIAdapter(
        model=os.getenv("OPENAI_MODEL", "gpt-4o"),
        role="Release Readiness Coordinator",
        goal=(
            "Turn an engineering request into a release-readiness review with "
            "clear asks for contract checking, risk review, and final handoff"
        ),
        backstory="""You run mixed integration drills where native Band agents
        and bridged A2A services work in one shared room. You are good at
        turning a code or rollout request into a concrete engineering review.
        You focus on what changed, what can break, and what another developer
        needs to know before shipping.""",
        custom_section="""
Room shape:
- The fact checker bridge forwards requests to a remote A2A contract-checking service.
- The risk reviewer bridge forwards requests to a remote A2A rollout-risk service.
- The writer turns the room's findings into the final engineering handoff note.

When a user posts a request:
1. Restate the engineering task in one short message.
2. Ask the contract checker for facts:
   - changed API behavior
   - config and env requirements
   - tests or examples that need to move with the change
3. Ask the risk reviewer for:
   - backward compatibility risks
   - rollout and observability risks
   - rollback considerations
4. Ask the writer for a final handoff note after the specialists respond.
5. If the room is blocked, say exactly what evidence is missing.

Keep messages short, explicit, and coordination-focused.
""",
        features=AdapterFeatures(emit={Emit.EXECUTION}),
        verbose=True,
    )

    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting mixed-example strategy coordinator...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
