from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "examples"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "examples", "gemini"))

from setup_logging import setup_logging
from band import Agent
from band.adapters import GeminiAdapter
from band.core.types import AdapterFeatures, Capability, Emit
from band.runtime.types import ContactEventConfig, ContactEventStrategy

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

    adapter = GeminiAdapter(
        model="gemini-2.5-flash",
        prompt=(
            "You are a contact management assistant. When the user asks you to "
            "manage contacts — list, add, remove, list requests, or respond to "
            "requests — use the appropriate contact tools."
        ),
        features=AdapterFeatures(
            capabilities={Capability.CONTACTS},
            emit={Emit.EXECUTION},
        ),
    )

    agent = Agent.from_config(
        "gem_contacts_test",
        adapter=adapter,
        contact_config=ContactEventConfig(strategy=ContactEventStrategy.DISABLED),
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting Gemini contacts agent (strategy=DISABLED)...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
