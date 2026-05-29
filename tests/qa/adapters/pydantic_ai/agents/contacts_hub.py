from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "examples"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "examples", "pydantic_ai"))

from setup_logging import setup_logging
from thenvoi import Agent
from thenvoi.adapters import PydanticAIAdapter
from thenvoi.core.types import AdapterFeatures, Capability, Emit
from thenvoi.runtime.types import ContactEventConfig, ContactEventStrategy

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

    adapter = PydanticAIAdapter(
        model="openai:gpt-5.4-mini",
        custom_section=(
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
        "pai_contacts_test",
        adapter=adapter,
        contact_config=ContactEventConfig(
            strategy=ContactEventStrategy.HUB_ROOM,
            broadcast_changes=True,
        ),
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting Pydantic AI contacts agent (strategy=HUB_ROOM, broadcast=True)...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
