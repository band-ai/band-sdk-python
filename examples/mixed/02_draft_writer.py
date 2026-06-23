# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[crewai]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Mixed-example CrewAI writer.

Starts the CrewAI agent that turns the room's findings into a polished final
engineering handoff after the coordinator, contract checker, and risk reviewer
weigh in.

Run with:
    uv run examples/mixed/02_draft_writer.py
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
from band.core.types import AdapterFeatures, Emit

logger = logging.getLogger(__name__)
CONFIG_PATH = Path(__file__).with_name("agents.yaml")


async def main() -> None:
    setup_logging()
    load_dotenv()

    ws_url = os.getenv("BAND_WS_URL", "wss://app.band.ai/api/v1/socket/websocket")
    rest_url = os.getenv("BAND_REST_URL", "https://app.band.ai")
    adapter = CrewAIAdapter(
        model=os.getenv("OPENAI_MODEL", "gpt-5.4-mini"),
        role="Engineering Handoff Writer",
        goal=(
            "Turn room input into a final engineering note that reflects the "
            "contract checker's facts and the risk reviewer's cautions"
        ),
        backstory="""You are the closer in a mixed-agent room. You listen for
        concrete implementation facts, rollout risks, and coordinator guidance,
        then write something another developer can act on immediately.""",
        custom_section="""
When the room is active:
1. Wait for the contract checker and risk reviewer if they are present.
2. Gather the strongest points from the room.
3. Produce the final output in this structure:
   - Summary
   - API or behavior changes
   - Config or environment changes
   - Risks and mitigations
   - Recommended next steps
4. Call out any unresolved assumption at the end.

Do not try to coordinate the room. Your job is to synthesize.
""",
        features=AdapterFeatures(emit={Emit.EXECUTION}),
        verbose=True,
    )

    agent = Agent.from_config(
        "mixed_writer_agent",
        config_path=CONFIG_PATH,
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting mixed-example engineering handoff writer...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
