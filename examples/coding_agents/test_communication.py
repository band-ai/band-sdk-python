#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk>=1.2.0,<2.0.0"]
# ///
"""Test inter-agent communication between planner and reviewer.

Creates a chat room, adds the reviewer, sends a test message mentioning it,
and lists the room messages so you can confirm delivery.

Reads credentials from the combined agent_config.yaml written by
create_agents.py, and this directory's .env (the same file Compose reads) for
BAND_REST_URL — so this script targets the same platform as the running stack.

Usage:
    uv run test_communication.py
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from band import LoggingStyle, LogSettings

# The bare message format only exists for the standard style, so the style is
# pinned rather than read from BAND_LOG_CONSOLE_STYLE.
LogSettings(log_console_style=LoggingStyle.STANDARD).for_application().configure(
    fmt="%(message)s"
)
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
ENV_PATH = SCRIPT_DIR / ".env"
CONFIG_PATH = SCRIPT_DIR / "agent_config.yaml"


async def main() -> None:
    from band.config import load_agent_config
    from band_rest import AsyncRestClient
    from band_rest.types import (
        ChatMessageRequest,
        ChatMessageRequestMentionsItem,
        ChatRoomRequest,
        ParticipantRequest,
    )

    # Match the platform Compose connects to (Compose reads the same .env).
    load_dotenv(ENV_PATH)

    # Load agent credentials from the combined file create_agents.py writes.
    # load_agent_config validates the file exists and the required fields are
    # present, and understands the keyed (planner:/reviewer:) format.
    _, planner_key = load_agent_config("planner", config_path=CONFIG_PATH)
    reviewer_id, _ = load_agent_config("reviewer", config_path=CONFIG_PATH)

    base_url = os.environ.get("BAND_REST_URL", "https://app.band.ai")

    # Use planner as the "orchestrator" to create the room
    client = AsyncRestClient(api_key=planner_key, base_url=base_url)

    # Step 1: Create a chat room
    logger.info("Creating chat room...")
    room_response = await client.agent_api_chats.create_agent_chat(
        chat=ChatRoomRequest()
    )
    room = room_response.data
    room_id = room.id
    logger.info("  Room created: %s", room_id)

    # Step 2: Add reviewer as participant
    logger.info("Adding Reviewer to room...")
    await client.agent_api_participants.add_agent_chat_participant(
        chat_id=room_id,
        participant=ParticipantRequest(participant_id=reviewer_id),
    )
    logger.info("  Reviewer added")

    # Give agents time to join the room via WebSocket
    logger.info("Waiting for agents to join room...")
    await asyncio.sleep(3)

    # Step 3: Send a test message mentioning reviewer
    logger.info("Sending test message...")
    mentions = [
        ChatMessageRequestMentionsItem(
            id=reviewer_id,
            name="Reviewer",
        ),
    ]

    msg_response = await client.agent_api_messages.create_agent_chat_message(
        chat_id=room_id,
        message=ChatMessageRequest(
            content="Hello @Reviewer! This is a test message from the planner. Please confirm you received this by saying 'acknowledged'.",
            mentions=mentions,
        ),
    )
    logger.info("  Message sent: %s", msg_response.data.id)

    # Step 4: Wait and check for responses
    logger.info("\nWaiting 30s for agent responses...")
    await asyncio.sleep(30)

    # List messages in the room to see responses
    logger.info("\n=== Messages in room ===")
    messages_response = await client.agent_api_messages.list_agent_messages(
        chat_id=room_id,
    )

    for msg in messages_response.data:
        sender = getattr(msg, "sender_name", None) or msg.sender_id
        content = msg.content[:120] if msg.content else "(empty)"
        msg_type = getattr(msg, "message_type", "unknown")
        logger.info("[%s] %s: %s", msg_type, sender, content)

    logger.info("\nRoom ID: %s", room_id)
    logger.info("Test complete! Check docker compose logs for full agent activity.")


if __name__ == "__main__":
    asyncio.run(main())
