# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[parlant,logging]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Basic Parlant agent example using the official Parlant SDK.

The adapter owns the Parlant server: it reserves free ports, boots the server
when the Band agent starts, and tears it down on stop. Guidelines declared with
``add_guideline`` are created on the live agent at startup, with the full Band
toolset attached by default.

Run with:
    uv run examples/parlant/01_basic_agent.py

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

# Agent description with detailed instructions
AGENT_DESCRIPTION = """You are a helpful, knowledgeable assistant in the Band multi-agent platform.

## Your Tools

1. **band_send_message**: Send messages to users or agents in the chat room. Requires @mentions.
2. **band_send_event**: Share your reasoning ('thought'), report errors ('error'), or progress ('task').
3. **band_lookup_peers**: Find available agents that can help with specific topics.
4. **band_add_participant**: Invite agents or users to the current chat room.
5. **band_remove_participant**: Remove participants from the room.
6. **band_get_participants**: See who's currently in the room.
7. **band_create_chatroom**: Create new rooms for specific discussions.

## How to Respond

- Give detailed, specific answers to questions
- Remember information the user shares about themselves
- Reference previous parts of the conversation when relevant
- Ask follow-up questions to better understand the user's needs
- Be friendly but substantive - avoid generic or vague responses

## When to Use Tools

- To respond to users: Use band_send_message with their name in mentions
- Before complex actions: Use band_send_event with type='thought' to explain your plan
- If you can't answer something: Use band_lookup_peers to find specialized agents, then band_add_participant
- When asked about the room: Use band_get_participants to see who's here
- For new discussions: Use band_create_chatroom to create a dedicated space
"""


def build_adapter() -> ParlantAdapter:
    """Build the Parlant adapter with basic guidelines (Band tools auto-attached)."""
    adapter = ParlantAdapter(
        name="Parlant",
        description=AGENT_DESCRIPTION,
        nlp_service=p.NLPServices.openai,  # requires OPENAI_API_KEY
    )

    # When user asks a question or needs help
    adapter.add_guideline(
        condition="User asks a question or needs help with something",
        action="Analyze the request. If you can answer directly, use band_send_message with the user's name in mentions. If you need to think through a complex problem, first use band_send_event with type='thought' to share your reasoning.",
    )

    # When user asks to add someone or wants specialized help
    adapter.add_guideline(
        condition="User asks to add someone to the chat, mentions a specific agent name, or asks for specialized help you can't provide",
        action="First use band_lookup_peers to find available agents. Then IMMEDIATELY call band_add_participant with the identifier parameter set to the exact identifier from the band_lookup_peers result. Do NOT ask for confirmation - just add them. If user wants multiple agents, call band_add_participant once for each.",
    )

    # When user asks about participants
    adapter.add_guideline(
        condition="User asks who is in the room, about participants, or who they're talking to",
        action="Use band_get_participants to list all current room members",
    )

    # When user wants to create a new room
    adapter.add_guideline(
        condition="User wants to create a new chat room, discussion space, or separate conversation",
        action="Use band_create_chatroom to create a dedicated space for the new topic",
    )

    # When user asks to remove someone
    adapter.add_guideline(
        condition="User asks to remove someone from the chat",
        action="Use band_remove_participant with the identifier parameter set to the exact identifier to remove",
    )

    return adapter


async def main() -> None:
    load_dotenv()

    adapter = build_adapter()

    logger.info("Starting Band agent with Parlant SDK (full tools)...")
    async with Agent.from_config(
        "parlant_agent",
        adapter=adapter,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
