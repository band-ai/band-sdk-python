# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[parlant]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Parlant agent with behavioral guidelines using the official Parlant SDK.

This example shows how to use Parlant's guideline system for controlled
agent behavior while the Band adapter supplies the platform contract and tools.

Run with:
    uv run examples/parlant/02_with_guidelines.py

See also: https://github.com/emcie-co/parlant/blob/develop/examples/travel_voice_agent.py
"""

from __future__ import annotations

import asyncio
import logging
import os

import parlant.sdk as p
from dotenv import load_dotenv

from setup_logging import setup_logging
from band import Agent
from band.adapters import ParlantAdapter

setup_logging()
logger = logging.getLogger(__name__)

CUSTOM_DESCRIPTION = """
You are a collaborative assistant in the Band multi-agent platform.

## Your Role
- Help users navigate multi-agent conversations
- Facilitate collaboration between different agents
- Manage conversations clearly when multiple participants are involved
- Keep responses focused and actionable

## Guidelines
1. Be proactive about suggesting relevant specialists when a request is outside your scope
2. Keep responses focused and actionable
3. Be clear about what happened and what still needs attention
4. Ask focused follow-up questions when you need more context
"""


async def setup_agent_with_guidelines(server: p.Server) -> p.Agent:
    """Create and configure a Parlant agent with comprehensive guidelines."""
    agent = await server.create_agent(
        name="Parlant",
        description=CUSTOM_DESCRIPTION,
    )

    await agent.create_guideline(
        condition="User asks a question or sends a message",
        action="Answer concisely and ask a focused follow-up when more context is needed.",
    )

    await agent.create_guideline(
        condition="User asks for help and you cannot directly provide it",
        action="Explain what kind of specialist would be useful and summarize the context they would need.",
    )

    await agent.create_guideline(
        condition="Conversation is ending or user says goodbye",
        action="Close warmly and briefly offer further help.",
    )

    return agent


async def main() -> None:
    load_dotenv()

    ws_url = os.getenv("BAND_WS_URL")
    rest_url = os.getenv("BAND_REST_URL")

    if not ws_url:
        raise ValueError("BAND_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("BAND_REST_URL environment variable is required")
    # Start Parlant server with OpenAI
    async with p.Server(
        port=0,
        tool_service_port=0,
        nlp_service=p.NLPServices.openai,
    ) as server:
        parlant_agent = await setup_agent_with_guidelines(server)
        logger.info("Parlant agent with guidelines created: %s", parlant_agent.id)

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

        logger.info(
            "Starting Band agent with Parlant SDK and comprehensive guidelines..."
        )
        await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
