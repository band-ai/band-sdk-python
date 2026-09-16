# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[parlant,logging]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Customer support agent using Parlant SDK with guidelines.

This example demonstrates a realistic customer support agent with purely
behavioral guidelines: each passes ``tools=[]`` to opt out of the default
Band toolset, so Parlant generates plain replies and the adapter forwards
them to the room.

Run with:
    uv run examples/parlant/03_support_agent.py

See also: https://github.com/emcie-co/parlant/blob/develop/examples/travel_voice_agent.py
"""

from __future__ import annotations

import asyncio
import logging

import parlant.sdk as p
from dotenv import load_dotenv

from band import Agent, configure_logging
from band.adapters import ParlantAdapter

configure_logging(
    logging.INFO, style="rich", extra_loggers={"band_parlant_agent": logging.INFO}
)
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

# Behavioral guidelines only — no tools. The adapter forwards Parlant's plain
# replies to the room, so these guidelines shape wording, not tool use.
SUPPORT_GUIDELINES = [
    (
        "Customer asks about refunds or returns",
        "Express empathy first, then ask for order details (order number, item) before providing refund information",
    ),
    (
        "Customer is frustrated or upset",
        "Acknowledge their frustration, apologize for any inconvenience, and focus on finding a solution",
    ),
    (
        "Customer asks a technical question",
        "Ask about their setup (device, OS, version) before troubleshooting",
    ),
    (
        "Issue cannot be resolved by this agent",
        "Explain the limitation clearly and offer to escalate to a specialist by adding them to the conversation",
    ),
    (
        "Customer provides positive feedback",
        "Thank them warmly and ask if there's anything else you can help with",
    ),
    (
        "Customer mentions urgency or deadline",
        "Prioritize their request and provide the fastest path to resolution",
    ),
]


def build_adapter() -> ParlantAdapter:
    """Build the support adapter with behavior-only guidelines."""
    adapter = ParlantAdapter(
        name="Support",
        description=SUPPORT_DESCRIPTION,
        nlp_service=p.NLPServices.openai,  # requires OPENAI_API_KEY
    )
    for condition, action in SUPPORT_GUIDELINES:
        adapter.add_guideline(condition=condition, action=action, tools=[])
    return adapter


async def main() -> None:
    load_dotenv()

    adapter = build_adapter()

    logger.info("Starting customer support agent with Parlant SDK...")
    async with Agent.from_config(
        "support_agent",
        adapter=adapter,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
