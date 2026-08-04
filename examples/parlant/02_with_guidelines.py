# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[parlant,logging]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Parlant agent with behavioral guidelines using the official Parlant SDK.

This example shows how to use Parlant's guideline system for controlled
agent behavior. Guidelines are declared on the adapter and created on the
live agent at startup, with the full Band toolset attached by default.

Run with:
    uv run examples/parlant/02_with_guidelines.py

See also: https://github.com/emcie-co/parlant/blob/develop/examples/travel_voice_agent.py
"""

from __future__ import annotations

import asyncio
import logging

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
- Manage participants in chat rooms
- Create new chat rooms when needed for specific topics

## Your Tools
- band_send_message: Respond to users (requires mentions)
- band_send_event: Share thoughts, errors, or task progress
- band_lookup_peers: Find available agents
- band_add_participant: Add agents/users to room
- band_remove_participant: Remove participants
- band_get_participants: List current participants
- band_create_chatroom: Create new rooms

## Guidelines
1. Be proactive about suggesting relevant agents to add
2. Keep responses focused and actionable
3. Always confirm actions taken with the user
4. Use band_send_event with type='thought' before complex actions
"""


def build_adapter() -> ParlantAdapter:
    """Build the Parlant adapter with comprehensive guidelines."""
    adapter = ParlantAdapter(
        name="Parlant",
        description=CUSTOM_DESCRIPTION,
        nlp_service=p.NLPServices.openai,  # requires OPENAI_API_KEY
    )

    # Communication guidelines
    adapter.add_guideline(
        condition="User asks a question or sends a message",
        action="Use band_send_message to respond, with the user's name in the mentions field",
    )

    adapter.add_guideline(
        condition="You are about to perform a complex action or multi-step process",
        action="First use band_send_event with type='thought' to explain what you're about to do and why",
    )

    # Participant management guidelines
    adapter.add_guideline(
        condition="User mentions a specific participant, agent name, or asks to add someone",
        action="First use band_lookup_peers to find available agents. Then IMMEDIATELY call band_add_participant with the name parameter set to the exact name from the band_lookup_peers result. Do NOT ask for confirmation - just add them. If user wants multiple agents, call band_add_participant once for each.",
    )

    adapter.add_guideline(
        condition="User asks about current participants or who is in the room",
        action="Use band_get_participants to list all current room members",
    )

    adapter.add_guideline(
        condition="User asks to remove someone from the chat",
        action="Use band_remove_participant with the name parameter set to the exact name to remove",
    )

    # Room management guidelines
    adapter.add_guideline(
        condition="User wants to create a new chat, discussion space, or separate topic",
        action="Use band_create_chatroom to create a dedicated space for the new topic",
    )

    # Error handling guideline
    adapter.add_guideline(
        condition="An error occurs or something goes wrong",
        action="Use band_send_event with type='error' to report the problem, then try to suggest alternatives",
    )

    # Conversation flow guidelines
    adapter.add_guideline(
        condition="User asks for help and you cannot directly provide it",
        action="Use band_lookup_peers to find specialized agents, explain your plan using band_send_event with type='thought', then add the most relevant agent",
    )

    adapter.add_guideline(
        condition="Conversation is ending or user says goodbye",
        action="Use band_send_message to summarize what was discussed and offer to help with anything else",
    )

    return adapter


async def main() -> None:
    load_dotenv()

    adapter = build_adapter()

    agent = Agent.from_config(
        "parlant_agent",
        adapter=adapter,
    )

    logger.info("Starting Band agent with Parlant SDK and comprehensive guidelines...")
    async with agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
