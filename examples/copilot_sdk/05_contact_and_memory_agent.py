#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[copilot_sdk]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Copilot SDK agent with contact and memory tools enabled.

This example shows a Copilot SDK adapter configured to:
- use contact tools through normal LLM tool calling
- enable memory tools for durable preferences and notes
- broadcast contact changes back into active rooms

Try prompts like:
- "List my contacts and check whether @alice is already connected."
- "Send a contact request to @alice with a short intro."
- "Remember that I want concise status updates."
- "What do you remember about my preferred update style?"

Inference runs BYOK on your Anthropic key (ANTHROPIC_API_KEY in .env).

Run with:
    uv run examples/copilot_sdk/05_contact_and_memory_agent.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from copilot import ProviderConfig
from dotenv import load_dotenv

# Add examples directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from setup_logging import setup_logging
from band import Agent
from band.adapters import CopilotSDKAdapter, CopilotSDKAdapterConfig
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
    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
    if not anthropic_api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable is required for BYOK")

    # The MEMORY/CONTACTS capabilities already inject full memory- and
    # contact-tool instructions into the system prompt; custom_section only
    # adds what the base prompt doesn't cover.
    adapter = CopilotSDKAdapter(
        CopilotSDKAdapterConfig(
            custom_section=(
                "When a [Contacts] system message reports that a contact was added "
                "or removed, treat it as fresh room context."
            ),
            # BYOK: inference runs on the Anthropic key; GitHub auth still
            # boots the Copilot runtime (base_url is required by the runtime).
            model="claude-haiku-4-5",
            provider=ProviderConfig(
                type="anthropic",
                base_url="https://api.anthropic.com",
                api_key=anthropic_api_key,
            ),
            github_token=os.getenv("GITHUB_TOKEN"),
            # Pin a unique per-example session prefix.
            session_id_prefix="band-copilot-contact-memory-",
        ),
        features=AdapterFeatures(
            capabilities={Capability.MEMORY, Capability.CONTACTS},
            emit={Emit.EXECUTION},
        ),
    )

    # Contact WebSocket events (requests arriving, contacts added/removed):
    # DISABLED = never react automatically — no auto-approve, no hub room;
    # the agent only touches contacts when a user asks it to via the tools
    # above. broadcast_changes keeps the LLM informed anyway: on a real
    # contact change the runtime injects a "[Contacts]: ..." system message
    # into active rooms (the custom_section teaches the model to use it).
    contact_config = ContactEventConfig(
        strategy=ContactEventStrategy.DISABLED,
        broadcast_changes=True,
    )

    agent = Agent.from_config(
        "copilot_contact_memory_agent",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
        contact_config=contact_config,
    )

    logger.info("Starting Copilot SDK contact-and-memory example agent")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
