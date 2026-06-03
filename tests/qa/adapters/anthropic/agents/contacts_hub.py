from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "examples"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "examples", "anthropic"))

from setup_logging import setup_logging
from band import Agent
from band.adapters import AnthropicAdapter
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

    adapter = AnthropicAdapter(
        model="claude-sonnet-4-5-20250929",
        prompt=(
            "You are a contact management assistant. When the user asks you to "
            "manage contacts — list, add, remove, list requests, or respond to "
            "requests — use the appropriate contact tools. When you receive a "
            "contact request in your hub room, evaluate the requester's message "
            "to decide whether to approve or reject. Reject suspicious or spammy "
            "requests."
        ),
        features=AdapterFeatures(
            capabilities={Capability.CONTACTS},
            emit={Emit.EXECUTION},
        ),
    )

    agent = Agent.from_config(
        "anth_contacts_test",
        adapter=adapter,
        contact_config=ContactEventConfig(
            strategy=ContactEventStrategy.HUB_ROOM,
            broadcast_changes=True,
        ),
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting Anthropic contacts agent (strategy=HUB_ROOM, broadcast=True)...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
