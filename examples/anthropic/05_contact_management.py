# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[anthropic]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Contact management example using the Anthropic adapter.

Demonstrates how to configure an agent with auto-approve contact handling
via the CALLBACK strategy. The agent can also use contact tools (list, add,
remove contacts and manage requests) through normal LLM tool calling.

Run with:
    uv run examples/anthropic/05_contact_management.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv

from setup_logging import setup_logging
from band import Agent
from band.adapters import AnthropicAdapter
from band.platform.event import ContactEvent, ContactRequestReceivedEvent
from band.runtime.contact_tools import ContactTools
from band.runtime.types import ContactEventConfig, ContactEventStrategy

setup_logging()
logger = logging.getLogger(__name__)


# NOTE: This example auto-approves ALL contact requests. That's fine if intended,
# but be aware that each accepted contact can send messages that trigger LLM
# inference. Alternatives:
# - Use HUB_ROOM strategy to let the agent's LLM decide per-request
# - Write a filtering on_event callback (e.g., only approve handles in an allowlist)
async def auto_approve_contacts(event: ContactEvent, tools: ContactTools) -> None:
    """Auto-approve all incoming contact requests."""
    if isinstance(event, ContactRequestReceivedEvent):
        logger.info("Auto-approving contact request from %s", event.payload.from_handle)
        await tools.respond_contact_request("approve", request_id=event.payload.id)


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
            "You are a helpful assistant with contact management capabilities.\n"
            "You can list, add, and remove contacts, and manage contact requests.\n"
            "Incoming contact requests are auto-approved."
        ),
    )

    contact_config = ContactEventConfig(
        strategy=ContactEventStrategy.CALLBACK,
        on_event=auto_approve_contacts,
        broadcast_changes=True,
    )

    agent = Agent.from_config(
        "anthropic_agent",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
        contact_config=contact_config,
    )

    logger.info("Starting Anthropic agent with contact management...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
