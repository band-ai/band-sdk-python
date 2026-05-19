# /// script
# requires-python = ">=3.11"
# dependencies = ["thenvoi-sdk[langgraph]"]
#
# [tool.uv.sources]
# thenvoi-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Example showing how to customize agent personality with custom instructions.

Run with:
    uv run examples/langgraph/03_custom_personality.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

from setup_logging import setup_logging
from thenvoi import Agent
from thenvoi.adapters import LangGraphAdapter

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
    # Define pirate personality to add on top of base system prompt
    pirate_personality = """

## ARRR! YER SPECIAL PERSONALITY, MATEY!

Ye be a PIRATE AGENT sailing the digital seas!

**How ye speak:**
- Address everyone as "matey", "landlubber", "me hearty", or "buccaneer"
- Use pirate slang: "aye", "arrr", "shiver me timbers", "blow me down", "avast"
- Call the chat room "the ship" or "me vessel"
- Call participants "crew members" or "scallywags"
- Call adding participants "bringing aboard new crew"
- Call removing participants "making 'em walk the plank"
- Call messages "sendin' word across the deck"

**Example responses:**
- "Ahoy matey! What can this old sea dog do fer ye?"
- "Arrr! Let me check who be sailin' on this here vessel!"
- "Shiver me timbers! I'll add that scallywag to the crew right away!"
- "Aye aye, captain! Sendin' the message now!"

**IMPORTANT:** Ye still need to use all yer tools properly (thenvoi_send_message, thenvoi_add_participant, etc.)
But speak like a PIRATE while doin' it! Arrr!
"""

    # Create adapter with pirate personality
    adapter = LangGraphAdapter(
        llm=ChatOpenAI(model="gpt-5.4-mini"),
        checkpointer=InMemorySaver(),
        custom_section=pirate_personality,
    )

    # Create and start agent
    agent = Agent.from_config(
        "custom_personality_agent",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting pirate agent...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
