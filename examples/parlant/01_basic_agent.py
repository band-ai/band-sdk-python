# /// script
# requires-python = ">=3.11"
# dependencies = ["thenvoi-sdk[parlant]"]
#
# [tool.uv.sources]
# thenvoi-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Basic Parlant agent example using the official Parlant SDK.

This example shows how to create a Thenvoi agent using the Parlant SDK
directly, with the full set of Thenvoi tools.

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
from thenvoi.integrations.parlant.tools import create_parlant_tools

setup_logging()
logger = logging.getLogger(__name__)

# Agent description with detailed instructions
AGENT_DESCRIPTION = """You are a helpful, knowledgeable assistant in the Thenvoi multi-agent platform.

## Your Tools

1. **thenvoi_send_message**: Send messages to users or agents in the chat room. Requires @mentions.
2. **thenvoi_send_event**: Share your reasoning ('thought'), report errors ('error'), or progress ('task').
3. **thenvoi_lookup_peers**: Find available agents that can help with specific topics.
4. **thenvoi_add_participant**: Invite agents or users to the current chat room.
5. **thenvoi_remove_participant**: Remove participants from the room.
6. **thenvoi_get_participants**: See who's currently in the room.
7. **thenvoi_create_chatroom**: Create new rooms for specific discussions.

## How to Respond

- Give detailed, specific answers to questions
- Remember information the user shares about themselves
- Reference previous parts of the conversation when relevant
- Ask follow-up questions to better understand the user's needs
- Be friendly but substantive - avoid generic or vague responses

## When to Use Tools

- To respond to users: Use thenvoi_send_message with their name in mentions
- Before complex actions: Use thenvoi_send_event with type='thought' to explain your plan
- If you can't answer something: Use thenvoi_lookup_peers to find specialized agents, then thenvoi_add_participant
- When asked about the room: Use thenvoi_get_participants to see who's here
- For new discussions: Use thenvoi_create_chatroom to create a dedicated space
"""


async def setup_agent_with_guidelines(
    server: p.Server,
    tools: list,
) -> p.Agent:
    """Create and configure a Parlant agent with basic guidelines and tools."""
    agent = await server.create_agent(
        name="Parlant",
        description=AGENT_DESCRIPTION,
    )

    # When user asks a question or needs help
    await agent.create_guideline(
        condition="User asks a question or needs help with something",
        action="Analyze the request. If you can answer directly, use thenvoi_send_message with the user's name in mentions. If you need to think through a complex problem, first use thenvoi_send_event with type='thought' to share your reasoning.",
        tools=tools,
    )

    # When user asks to add someone or wants specialized help
    await agent.create_guideline(
        condition="User asks to add someone to the chat, mentions a specific agent name, or asks for specialized help you can't provide",
        action="First use thenvoi_lookup_peers to find available agents. Then IMMEDIATELY call thenvoi_add_participant with the name parameter set to the exact name from the thenvoi_lookup_peers result. Do NOT ask for confirmation - just add them. If user wants multiple agents, call thenvoi_add_participant once for each.",
        tools=tools,
    )

    # When user asks about participants
    await agent.create_guideline(
        condition="User asks who is in the room, about participants, or who they're talking to",
        action="Use thenvoi_get_participants to list all current room members",
        tools=tools,
    )

    # When user wants to create a new room
    await agent.create_guideline(
        condition="User wants to create a new chat room, discussion space, or separate conversation",
        action="Use thenvoi_create_chatroom to create a dedicated space for the new topic",
        tools=tools,
    )

    # When user asks to remove someone
    await agent.create_guideline(
        condition="User asks to remove someone from the chat",
        action="Use thenvoi_remove_participant with the name parameter set to the exact name to remove",
        tools=tools,
    )

    return agent


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
        # Create Parlant tools INSIDE server context
        parlant_tools = create_parlant_tools()
        logger.info(
            "Created %s Parlant tools: %s",
            len(parlant_tools),
            [t.tool.name for t in parlant_tools],
        )

        # Create Parlant agent with guidelines and tools
        parlant_agent = await setup_agent_with_guidelines(server, parlant_tools)
        logger.info("Parlant agent created: %s", parlant_agent.id)

        # Create adapter using Parlant SDK directly
        adapter = ParlantAdapter(
            server=server,
            parlant_agent=parlant_agent,
        )

        # Create and start Thenvoi agent
        agent = Agent.from_config(
            "parlant_agent",
            adapter=adapter,
            ws_url=ws_url,
            rest_url=rest_url,
        )

        logger.info("Starting Thenvoi agent with Parlant SDK (full tools)...")
        await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
