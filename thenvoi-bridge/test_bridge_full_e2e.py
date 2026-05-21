"""Full end-to-end bridge test.

Starts the bridge components, simulates agent 2 sending an @mention
via REST, the bridge receives it via WebSocket, routes to the
LangChainHandler, which POSTs to the LLM server, and the response
is posted back to the platform.

Prerequisites:
    1. Start the echo server:
       cd thenvoi-bridge && python echo_server.py

    2. Set environment variables in .env.test:
       TEST_AGENT_ID, THENVOI_API_KEY, THENVOI_BASE_URL, THENVOI_WS_URL

    3. Run this script (from repo root):
       uv run python thenvoi-bridge/test_bridge_full_e2e.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from thenvoi.client.rest import (
    AsyncRestClient,
    ChatRoomRequest,
    DEFAULT_REQUEST_OPTIONS,
)
from thenvoi.client.streaming import Mention, MessageCreatedPayload, MessageMetadata
from thenvoi.platform.link import ThenvoiLink
from thenvoi.runtime.tools import AgentTools

from bridge_core.router import MentionRouter
from bridge_core.session import InMemorySessionStore
from handlers.chain import LangChainHandler

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
)
logger = logging.getLogger(__name__)

ECHO_SERVER_URL = "http://localhost:8000"


async def main() -> None:
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env.test")
    load_dotenv(env_path)

    agent_id = os.environ.get("TEST_AGENT_ID")
    api_key = os.environ.get("THENVOI_API_KEY")
    rest_url = os.environ.get("THENVOI_BASE_URL", "https://app.band.ai")
    ws_url = os.environ.get(
        "THENVOI_WS_URL", "wss://app.band.ai/api/v1/socket/websocket"
    )

    if not agent_id or not api_key:
        raise ValueError("TEST_AGENT_ID and THENVOI_API_KEY must be set in .env.test")

    logger.info("Agent ID: %s", agent_id)
    logger.info("REST URL: %s", rest_url)
    logger.info("WS URL: %s", ws_url)
    logger.info("Echo Server: %s\n", ECHO_SERVER_URL)

    rest = AsyncRestClient(api_key=api_key, base_url=rest_url)

    # --- Setup: get or create a chat room ---
    rooms_response = await rest.agent_api_chats.list_agent_chats(
        request_options=DEFAULT_REQUEST_OPTIONS,
    )
    if rooms_response.data:
        room_id = rooms_response.data[0].id
        logger.info("Reusing existing room: %s", room_id)
    else:
        room_response = await rest.agent_api_chats.create_agent_chat(
            chat=ChatRoomRequest(),
            request_options=DEFAULT_REQUEST_OPTIONS,
        )
        room_id = room_response.data.id
        logger.info("Created room: %s", room_id)

    # --- Fetch participants ---
    participants_response = (
        await rest.agent_api_participants.list_agent_chat_participants(
            chat_id=room_id,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )
    )
    participants = []
    if participants_response.data:
        participants = [
            {
                "id": p.id,
                "name": p.name,
                "type": p.type,
                "handle": getattr(p, "handle", None),
            }
            for p in participants_response.data
        ]
    logger.info("Participants: %s", participants)

    # --- Connect WebSocket ---
    link = ThenvoiLink(
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )
    await link.connect()
    logger.info("WebSocket connected")

    await link.subscribe_room(room_id)
    logger.info("Subscribed to room %s", room_id)

    # --- Setup router with LangChainHandler ---
    handler = LangChainHandler(base_url=ECHO_SERVER_URL)
    store = InMemorySessionStore()

    router = MentionRouter(
        agent_mapping={"echo_agent": "echo"},
        handlers={"echo": handler},
        session_store=store,
        agent_id=agent_id,
        link=link,
    )

    # --- Simulate incoming message (as if received via WebSocket) ---
    sender = next(
        (p for p in participants if p["type"] == "User"),
        participants[0] if participants else {"id": "user-1", "name": "Test User"},
    )

    payload = MessageCreatedPayload(
        id="e2e-test-msg-001",
        content="Hello from the full E2E test!",
        message_type="text",
        sender_id=sender["id"],
        sender_type=sender.get("type", "User"),
        chat_room_id=room_id,
        thread_id=None,
        inserted_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
        metadata=MessageMetadata(
            mentions=[Mention(id="echo-id", username="echo_agent")],
            status="sent",
        ),
    )

    tools = AgentTools(room_id=room_id, rest=rest, participants=participants)

    logger.info("Dispatching message through router...")
    try:
        await router.route(payload, room_id, tools, sender_name=sender.get("name"))
        logger.info("E2E test PASSED — message dispatched and response posted")
    except Exception:
        logger.exception("E2E test FAILED")
        sys.exit(1)
    finally:
        await handler.close()
        await link.disconnect()
        logger.info("Cleanup complete")


if __name__ == "__main__":
    asyncio.run(main())
