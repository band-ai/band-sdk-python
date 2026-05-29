from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "examples"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "examples", "gemini"))

from setup_logging import setup_logging
from thenvoi import Agent
from thenvoi.adapters import GeminiAdapter
from thenvoi.core.types import AdapterFeatures, Capability, Emit
from thenvoi.platform.event import ContactRequestReceivedEvent
from thenvoi.runtime.types import ContactEventConfig, ContactEventStrategy

setup_logging()
logger = logging.getLogger(__name__)

APPROVED_HANDLE_PATTERNS = ["nir/websocket-*", "nir/ws-*"]


async def whitelist_approve(event, tools):
    if isinstance(event, ContactRequestReceivedEvent):
        handle = (event.payload.from_handle or "").lstrip("@")
        if any(fnmatch.fnmatch(handle, pat) for pat in APPROVED_HANDLE_PATTERNS):
            logger.info("Whitelist match — approving %s", handle)
            await tools.respond_contact_request("approve", request_id=event.payload.id)
        else:
            logger.info("Not on whitelist — rejecting %s", handle)
            await tools.respond_contact_request("reject", request_id=event.payload.id)


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
        contact_config=ContactEventConfig(
            strategy=ContactEventStrategy.CALLBACK,
            on_event=whitelist_approve,
            broadcast_changes=True,
        ),
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting Gemini contacts agent (strategy=CALLBACK, broadcast=True)...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
