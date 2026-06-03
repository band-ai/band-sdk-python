# /// script
# requires-python = ">=3.11"
# dependencies = ["thenvoi-sdk[parlant]"]
#
# [tool.uv.sources]
# thenvoi-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Basic Parlant agent example using the official Parlant SDK.

This example shows how to create a Band agent using the Parlant SDK directly.

Run with:
    uv run examples/parlant/01_basic_agent.py

See also: https://github.com/emcie-co/parlant/blob/develop/examples/travel_voice_agent.py
"""

from __future__ import annotations

import asyncio
import logging
import os

import parlant.sdk as p
from dotenv import load_dotenv

from setup_logging import setup_logging
from thenvoi import Agent
from thenvoi.adapters import ParlantAdapter

setup_logging()
logger = logging.getLogger(__name__)

AGENT_DESCRIPTION = """
You are a helpful, knowledgeable assistant.

## How to Respond

- Give detailed, specific answers to questions
- Remember information the user shares about themselves
- Reference previous parts of the conversation when relevant
- Ask follow-up questions to better understand the user's needs
- Be friendly but substantive; avoid generic or vague responses
"""


async def main() -> None:
    load_dotenv()

    ws_url = os.getenv("THENVOI_WS_URL")
    rest_url = os.getenv("THENVOI_REST_URL")

    if not ws_url:
        raise ValueError("THENVOI_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("THENVOI_REST_URL environment variable is required")
    # Start Parlant server with OpenAI (requires OPENAI_API_KEY env var)
    async with p.Server(nlp_service=p.NLPServices.openai) as server:
        parlant_agent = await server.create_agent(
            name="Parlant",
            description=AGENT_DESCRIPTION,
        )
        logger.info("Parlant agent created: %s", parlant_agent.id)

        adapter = ParlantAdapter(
            server=server,
            parlant_agent=parlant_agent,
        )

        agent = Agent.from_config(
            "parlant_agent",
            adapter=adapter,
            ws_url=ws_url,
            rest_url=rest_url,
        )

        logger.info("Starting Band agent with Parlant SDK...")
        await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
