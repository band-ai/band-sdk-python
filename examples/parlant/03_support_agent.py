# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[parlant,logging]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Customer support agent using Parlant SDK with guidelines.

This example demonstrates a realistic customer support agent with
behavioral guidelines using the Parlant SDK directly.

Run with:
    uv run examples/parlant/03_support_agent.py

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

SUPPORT_DESCRIPTION = """
You are a customer support agent for TechCo Solutions.

Your responsibilities:
- Handle customer inquiries with professionalism and empathy
- Resolve issues efficiently while maintaining quality
- Escalate complex issues to specialists when needed
- Document interactions for follow-up

Communication style:
- Friendly but professional
- Clear and concise
- Solution-focused
- Proactive about next steps

Remember:
- Customer satisfaction is the top priority
- Never make promises you can't keep
- Always follow up on commitments
"""


async def setup_support_agent(server: p.Server) -> p.Agent:
    """Create and configure a customer support agent with guidelines."""
    agent = await server.create_agent(
        name="Support",
        description=SUPPORT_DESCRIPTION,
    )

    # Add support-specific guidelines
    await agent.create_guideline(
        condition="Customer asks about refunds or returns",
        action="Express empathy first, then ask for order details (order number, item) before providing refund information",
    )

    await agent.create_guideline(
        condition="Customer is frustrated or upset",
        action="Acknowledge their frustration, apologize for any inconvenience, and focus on finding a solution",
    )

    await agent.create_guideline(
        condition="Customer asks a technical question",
        action="Ask about their setup (device, OS, version) before troubleshooting",
    )

    await agent.create_guideline(
        condition="Issue cannot be resolved by this agent",
        action="Explain the limitation clearly and offer to escalate to a specialist by adding them to the conversation",
    )

    await agent.create_guideline(
        condition="Customer provides positive feedback",
        action="Thank them warmly and ask if there's anything else you can help with",
    )

    await agent.create_guideline(
        condition="Customer mentions urgency or deadline",
        action="Prioritize their request and provide the fastest path to resolution",
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
    # Start Parlant server
    async with p.Server(
        port=0,
        tool_service_port=0,
        nlp_service=p.NLPServices.openai,
    ) as server:
        # Create support agent with guidelines
        parlant_agent = await setup_support_agent(server)
        logger.info("Support agent created: %s", parlant_agent.id)

        # Create adapter using Parlant SDK directly
        adapter = ParlantAdapter(
            server=server,
            parlant_agent=parlant_agent,
        )

        # Create and start Band agent
        agent = Agent.from_config(
            "support_agent",
            adapter=adapter,
            ws_url=ws_url,
            rest_url=rest_url,
        )

        logger.info("Starting customer support agent with Parlant SDK...")
        await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
