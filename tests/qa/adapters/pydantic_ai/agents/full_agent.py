from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "examples"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "examples", "pydantic_ai"))

from setup_logging import setup_logging
from band import Agent
from band.adapters import PydanticAIAdapter
from band.core.types import AdapterFeatures, Capability, Emit

setup_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()

    ws_url = os.getenv("BAND_WS_URL")
    rest_url = os.getenv("BAND_REST_URL")
    if not ws_url:
        raise ValueError("BAND_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("BAND_REST_URL environment variable is required")

    adapter = PydanticAIAdapter(
        model="openai:gpt-5.4-mini",
        custom_section=(
            "You are a full-featured assistant with access to memory management, "
            "contact management, and all platform tools. Use them when appropriate."
        ),
        features=AdapterFeatures(
            capabilities={Capability.MEMORY, Capability.CONTACTS},
            emit={Emit.EXECUTION},
        ),
    )

    agent = Agent.from_config(
        "pai_full_test",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting Pydantic AI full agent...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
