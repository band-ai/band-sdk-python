# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[langgraph]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""Start a 20 Questions Arena game as a user by creating a room, adding all agents, and sending a message.

Run with:
    uv run examples/20-questions-arena/start_game.py <user_api_key>
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv
from band_rest import AsyncRestClient, ChatMessageRequest, ParticipantRequest
from band_rest.human_api_chats.types.create_my_chat_room_request_chat import (
    CreateMyChatRoomRequestChat,
)
from band_rest.types import ChatMessageRequestMentionsItem as Mention

from setup_logging import setup_logging
from band.config import load_agent_config

logger = logging.getLogger(__name__)


def _load_agent_id(config_key: str) -> str | None:
    """Load agent_id from agent_config.yaml, returning None if missing."""
    try:
        agent_id, _ = load_agent_config(config_key)
        return agent_id
    except (ValueError, KeyError):
        return None


async def start_game(user_api_key: str) -> str:
    rest_url = os.getenv("BAND_REST_URL")
    if not rest_url:
        raise ValueError("BAND_REST_URL environment variable is required")

    thinker_agent_id = _load_agent_id("arena_thinker")
    if not thinker_agent_id:
        raise ValueError("arena_thinker config key is required in agent_config.yaml")

    guesser_agent_ids = []
    for key in ("arena_guesser", "arena_guesser_2", "arena_guesser_3"):
        gid = _load_agent_id(key)
        if gid:
            guesser_agent_ids.append(gid)
        else:
            logger.info("Skipping %s (not configured)", key)

    if not guesser_agent_ids:
        raise ValueError(
            "At least one guesser config key (arena_guesser, arena_guesser_2, arena_guesser_3) "
            "is required in agent_config.yaml"
        )

    client = AsyncRestClient(api_key=user_api_key, base_url=rest_url)

    # Create a chat room
    chat_resp = await client.human_api_chats.create_my_chat_room(
        chat=CreateMyChatRoomRequestChat(),
    )
    chat_id = chat_resp.data.id
    logger.info("Created chat room: %s", chat_id)

    # Add thinker agent to the room
    await client.human_api_participants.add_my_chat_participant(
        chat_id,
        participant=ParticipantRequest(
            participant_id=thinker_agent_id,
            role="member",
        ),
    )
    logger.info("Added Thinker to room")

    # Add all guesser agents to the room
    for guesser_id in guesser_agent_ids:
        await client.human_api_participants.add_my_chat_participant(
            chat_id,
            participant=ParticipantRequest(
                participant_id=guesser_id,
                role="member",
            ),
        )
    logger.info("Added %s guessers to room", len(guesser_agent_ids))

    # Get participants to build mention list
    parts_resp = await client.human_api_participants.list_my_chat_participants(chat_id)

    # Build a map of agent_id -> participant info
    agents = {}
    for p in parts_resp.data:
        pid = str(p.id)
        agents[pid] = p

    thinker = agents.get(thinker_agent_id)
    thinker_name = thinker.name if thinker else "Thinker"
    thinker_id = str(thinker.id) if thinker else thinker_agent_id

    # Build mentions for thinker + all guessers
    mentions = [Mention(id=thinker_id, name=thinker_name)]
    guesser_names = []
    for gid in guesser_agent_ids:
        g = agents.get(gid)
        if g:
            mentions.append(Mention(id=str(g.id), name=g.name))
            guesser_names.append(g.name)

    logger.info("Thinker: %s", thinker_name)
    logger.info("Guessers: %s", ", ".join(guesser_names))

    # Send "start a game" message mentioning everyone
    msg_resp = await client.human_api_messages.send_my_chat_message(
        chat_id,
        message=ChatMessageRequest(
            content=f"@{thinker_name} start a new game of 20 questions with all the guessers in this room!",
            mentions=mentions,
        ),
    )
    logger.info("Sent start message: %s", msg_resp.data.id)
    return chat_id


async def main() -> None:
    load_dotenv()
    setup_logging(agent_tag="start_game")

    user_api_key = sys.argv[1] if len(sys.argv) > 1 else ""
    if not user_api_key:
        raise ValueError("Usage: python start_game.py <user_api_key>")

    chat_id = await start_game(user_api_key)
    logger.info("Game started in room: %s", chat_id)


if __name__ == "__main__":
    asyncio.run(main())
