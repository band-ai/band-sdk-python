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
agent behavior with the full Band toolset.

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
from band.integrations.parlant.tools import create_parlant_tools

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


async def setup_agent_with_guidelines(
    server: p.Server,
    tools: list,
) -> p.Agent:
    """Create and configure a Parlant agent with comprehensive guidelines and tools."""
    agent = await server.create_agent(
        name="Parlant",
        description=CUSTOM_DESCRIPTION,
    )

    # Communication guidelines
    await agent.create_guideline(
        condition="User asks a question or sends a message",
        action="Use band_send_message to respond, with the user's name in the mentions field",
        tools=tools,
    )

    await agent.create_guideline(
        condition="You are about to perform a complex action or multi-step process",
        action="First use band_send_event with type='thought' to explain what you're about to do and why",
        tools=tools,
    )

    # Participant management guidelines
    await agent.create_guideline(
        condition="User mentions a specific participant, agent name, or asks to add someone",
        action="First use band_lookup_peers to find available agents. Then IMMEDIATELY call band_add_participant with the name parameter set to the exact name from the band_lookup_peers result. Do NOT ask for confirmation - just add them. If user wants multiple agents, call band_add_participant once for each.",
        tools=tools,
    )

    await agent.create_guideline(
        condition="User asks about current participants or who is in the room",
        action="Use band_get_participants to list all current room members",
        tools=tools,
    )

    await agent.create_guideline(
        condition="User asks to remove someone from the chat",
        action="Use band_remove_participant with the name parameter set to the exact name to remove",
        tools=tools,
    )

    # Room management guidelines
    await agent.create_guideline(
        condition="User wants to create a new chat, discussion space, or separate topic",
        action="Use band_create_chatroom to create a dedicated space for the new topic",
        tools=tools,
    )

    # Error handling guideline
    await agent.create_guideline(
        condition="An error occurs or something goes wrong",
        action="Use band_send_event with type='error' to report the problem, then try to suggest alternatives",
        tools=tools,
    )

    # Conversation flow guidelines
    await agent.create_guideline(
        condition="User asks for help and you cannot directly provide it",
        action="Use band_lookup_peers to find specialized agents, explain your plan using band_send_event with type='thought', then add the most relevant agent",
        tools=tools,
    )

    await agent.create_guideline(
        condition="Conversation is ending or user says goodbye",
        action="Use band_send_message to summarize what was discussed and offer to help with anything else",
        tools=tools,
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
    async with p.Server(nlp_service=p.NLPServices.openai) as server:
        # Create Parlant tools INSIDE server context
        parlant_tools = create_parlant_tools()
        logger.info(
            "Created %s Parlant tools: %s",
            len(parlant_tools),
            [t.tool.name for t in parlant_tools],
        )

        # Create Parlant agent with comprehensive guidelines and tools
        parlant_agent = await setup_agent_with_guidelines(server, parlant_tools)
        logger.info("Parlant agent with guidelines created: %s", parlant_agent.id)

        # Create adapter using Parlant SDK directly
        adapter = ParlantAdapter(
            server=server,
            parlant_agent=parlant_agent,
        )

        # Create and start Band agent
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
