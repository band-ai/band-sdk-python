from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "examples"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "examples", "gemini"))

from setup_logging import setup_logging
from thenvoi import Agent
from thenvoi.adapters import GeminiAdapter
from thenvoi.core.types import AdapterFeatures, Capability, Emit

setup_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()

    ws_url = os.getenv("THENVOI_WS_URL")
    rest_url = os.getenv("THENVOI_REST_URL")
    if not ws_url:
        raise ValueError("THENVOI_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("THENVOI_REST_URL environment variable is required")

    adapter = GeminiAdapter(
        model="gemini-2.5-flash",
        prompt=(
            "You are a full-featured assistant with access to memory management, "
            "contact management, and all platform tools. Use them when appropriate."
        ),
        features=AdapterFeatures(
            capabilities={Capability.MEMORY, Capability.CONTACTS},
            emit={Emit.EXECUTION},
        ),
    )

    agent = Agent.from_config(
        "gem_full_test",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting Gemini full agent...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
