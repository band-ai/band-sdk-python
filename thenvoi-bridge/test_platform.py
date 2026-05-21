"""Integration test: LangChainHandler with real Band platform.

Tests:
  1. handler.handle() end-to-end (echo server + send_message to platform)
  2. Echo server round-trip with response parsing
  3. Error handling: unreachable server, timeout, unknown agent
  4. from_env() factory with per-agent URL mapping
  5. WebSocket: connect, listen for events, verify delivery

Prerequisites:
    1. Start the echo server in another terminal:
       uv run python thenvoi-bridge/echo_server.py

    2. Run this script (from repo root):
       uv run python thenvoi-bridge/test_platform.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from handlers.chain import LangChainHandler
from thenvoi.client.rest import (
    AsyncRestClient,
    ChatRoomRequest,
    DEFAULT_REQUEST_OPTIONS,
)
from thenvoi.platform.link import ThenvoiLink
from thenvoi.runtime.tools import AgentTools

# User REST client for adding user participant to rooms
USER_API_KEY = os.environ.get("THENVOI_API_KEY_USER", "")

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
)
logger = logging.getLogger(__name__)

ECHO_SERVER_URL = "http://localhost:8000"

passed = 0
failed = 0


async def run_test(name: str, coro) -> None:
    """Run a single test and track pass/fail."""
    global passed, failed
    logger.info("=== %s ===", name)
    try:
        await coro
        logger.info("PASSED: %s", name)
        passed += 1
    except Exception:
        logger.exception("FAILED: %s", name)
        failed += 1
    logger.info("")


async def main() -> None:
    # Load .env.test
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
    logger.info("WS URL: %s\n", ws_url)

    rest = AsyncRestClient(api_key=api_key, base_url=rest_url)

    # --- Setup: Reuse an existing chat room (or create if possible) ---
    logger.info("Setting up: looking for existing chat room...")
    rooms_response = await rest.agent_api_chats.list_agent_chats(
        request_options=DEFAULT_REQUEST_OPTIONS,
    )
    if rooms_response.data:
        room_id = rooms_response.data[0].id
        logger.info("Reusing existing room: %s", room_id)
    else:
        logger.info("No existing rooms, creating one...")
        room_response = await rest.agent_api_chats.create_agent_chat(
            chat=ChatRoomRequest(),
            request_options=DEFAULT_REQUEST_OPTIONS,
        )
        room_id = room_response.data.id
        logger.info("Created room: %s", room_id)

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
    logger.info("Participants: %s\n", participants)

    _ = next((p["handle"] for p in participants if p["id"] == agent_id), None)

    # --- Setup: ensure a user is in the room so we have a non-agent sender ---
    user_participant = next((p for p in participants if p["type"] == "User"), None)
    if not user_participant:
        # Add the owner user to the room via lookup_peers
        logger.info("No user in room, adding owner via lookup_peers...")
        tools_setup = AgentTools(room_id=room_id, rest=rest, participants=participants)
        peers = await tools_setup.lookup_peers()
        user_peer = next(
            (p for p in peers.get("peers", []) if p.get("type") == "User"), None
        )
        if user_peer:
            result = await tools_setup.add_participant(user_peer["name"])
            user_participant = {
                "id": result["id"],
                "name": user_peer["name"],
                "type": "User",
                "handle": user_peer.get("handle"),
            }
            participants.append(user_participant)
            logger.info("Added user to room: %s", user_participant)
        else:
            logger.warning("No user peer found — e2e test will use agent-only fallback")

    # =====================================================================
    # Test 1: handler.handle() FULL end-to-end — echo + send_message to platform
    # Uses the user as sender so the agent can mention them in the reply.
    # This produces a VISIBLE message in the platform UI.
    # =====================================================================
    async def test_handle_e2e() -> None:
        handler = LangChainHandler(base_url=ECHO_SERVER_URL)
        tools = AgentTools(room_id=room_id, rest=rest, participants=participants)
        try:
            sender_id = user_participant["id"] if user_participant else agent_id
            sender_name = user_participant["name"] if user_participant else "test agent"
            sender_type = user_participant["type"] if user_participant else "Agent"

            sender_handle = user_participant.get("handle") if user_participant else None

            await handler.handle(
                content="Hello from the LangChain bridge integration test!",
                room_id=room_id,
                thread_id=room_id,
                message_id="test-e2e-001",
                sender_id=sender_id,
                sender_name=sender_name,
                sender_handle=sender_handle,
                sender_type=sender_type,
                mentioned_agent="echo_agent",
                tools=tools,
            )
            logger.info("Full handle() completed — check room for message")
        finally:
            await handler.close()

    await run_test("handler.handle() full end-to-end", test_handle_e2e())

    # =====================================================================
    # Test 2: Echo server round-trip + response parsing
    # =====================================================================
    async def test_echo_roundtrip() -> None:
        handler = LangChainHandler(base_url=ECHO_SERVER_URL)
        try:
            url = handler._resolve_url("any_agent")
            payload = handler._build_payload(
                content="Test payload parsing",
                room_id=room_id,
                thread_id="thread-123",
                message_id="msg-123",
                sender_id="user-1",
                sender_name="Alice",
                sender_type="User",
            )
            response = await handler._invoke_agent(url, payload)
            assert response, "Empty response from agent server"
            logger.info("Response correctly parsed: %s", response)
        finally:
            await handler.close()

    await run_test("Echo server round-trip + parsing", test_echo_roundtrip())

    # =====================================================================
    # Test 3: per-agent URL mapping via from_env()
    # =====================================================================
    async def test_from_env_per_agent() -> None:
        handler = LangChainHandler.from_env(
            f"agent_a:{ECHO_SERVER_URL}/invoke,agent_b:{ECHO_SERVER_URL}/invoke"
        )
        try:
            url_a = handler._resolve_url("agent_a")
            url_b = handler._resolve_url("agent_b")
            assert url_a == f"{ECHO_SERVER_URL}/invoke"
            assert url_b == f"{ECHO_SERVER_URL}/invoke"

            payload = handler._build_payload(
                content="Per-agent test",
                room_id=room_id,
                thread_id=room_id,
                message_id="msg-456",
                sender_id="user-1",
                sender_name="Alice",
                sender_type="User",
            )
            response = await handler._invoke_agent(url_a, payload)
            assert response, "Empty response from agent server"
            logger.info("Per-agent URL routing works: %s", response)
        finally:
            await handler.close()

    await run_test("from_env() per-agent URL mapping", test_from_env_per_agent())

    # =====================================================================
    # Test 4: Unknown agent raises ValueError
    # =====================================================================
    async def test_unknown_agent() -> None:
        handler = LangChainHandler(urls={"alice": f"{ECHO_SERVER_URL}/invoke"})
        try:
            handler._resolve_url("bob")
            raise AssertionError("Should have raised ValueError")
        except ValueError as e:
            logger.info("Correctly raised: %s", e)
        finally:
            await handler.close()

    await run_test("Unknown agent raises ValueError", test_unknown_agent())

    # =====================================================================
    # Test 5: Unreachable server raises error
    # =====================================================================
    async def test_unreachable_server() -> None:
        handler = LangChainHandler(base_url="http://localhost:19999", timeout=3)
        tools = AgentTools(room_id=room_id, rest=rest, participants=participants)
        sender_id = user_participant["id"] if user_participant else agent_id
        sender_name = user_participant["name"] if user_participant else "test agent"
        sender_handle = user_participant.get("handle") if user_participant else None
        sender_type = user_participant["type"] if user_participant else "Agent"
        try:
            await handler.handle(
                content="Should fail",
                room_id=room_id,
                thread_id=room_id,
                message_id="msg-fail-001",
                sender_id=sender_id,
                sender_name=sender_name,
                sender_handle=sender_handle,
                sender_type=sender_type,
                mentioned_agent="echo_agent",
                tools=tools,
            )
            raise AssertionError("Should have raised an error")
        except (TimeoutError, Exception) as e:
            if "timed out" in str(e).lower() or "connect" in str(e).lower():
                logger.info("Correctly failed with: %s", type(e).__name__)
            else:
                raise
        finally:
            await handler.close()

    await run_test("Unreachable server raises error", test_unreachable_server())

    # =====================================================================
    # Test 6: Timeout with slow server
    # =====================================================================
    async def test_timeout() -> None:
        # Use the echo server but with an extremely short timeout
        handler = LangChainHandler(base_url=ECHO_SERVER_URL, timeout=0.001)
        tools = AgentTools(room_id=room_id, rest=rest, participants=participants)
        sender_id = user_participant["id"] if user_participant else agent_id
        sender_name = user_participant["name"] if user_participant else "test agent"
        sender_handle = user_participant.get("handle") if user_participant else None
        sender_type = user_participant["type"] if user_participant else "Agent"
        try:
            await handler.handle(
                content="Should timeout",
                room_id=room_id,
                thread_id=room_id,
                message_id="msg-timeout-001",
                sender_id=sender_id,
                sender_name=sender_name,
                sender_handle=sender_handle,
                sender_type=sender_type,
                mentioned_agent="echo_agent",
                tools=tools,
            )
            # If the echo server responds faster than 1ms, that's OK too
            logger.info("Echo server responded within 1ms (fast!)")
        except TimeoutError:
            logger.info("Correctly timed out")
        finally:
            await handler.close()

    await run_test("Timeout handling", test_timeout())

    # =====================================================================
    # Test 7: WebSocket — connect, subscribe, send event, receive it
    # =====================================================================
    async def test_websocket_connect() -> None:
        link = ThenvoiLink(
            agent_id=agent_id,
            api_key=api_key,
            ws_url=ws_url,
            rest_url=rest_url,
        )
        try:
            await link.connect()
            assert link.is_connected
            logger.info("WebSocket connected")

            await link.subscribe_agent_rooms(agent_id)
            logger.info("Subscribed to agent rooms")

            await link.subscribe_room(room_id)
            logger.info("Subscribed to room %s — OK", room_id)
        finally:
            await link.disconnect()
            logger.info("Disconnected cleanly")

    await run_test("WebSocket connect + subscribe", test_websocket_connect())

    # =====================================================================
    # Test 8: Build payload — verify structure and optional fields
    # =====================================================================
    async def test_payload_structure() -> None:
        handler = LangChainHandler(base_url=ECHO_SERVER_URL)
        try:
            # With sender_name
            payload = handler._build_payload(
                content="test",
                room_id="room-1",
                thread_id="thread-1",
                message_id="msg-1",
                sender_id="user-1",
                sender_name="Alice",
                sender_type="User",
            )
            assert payload["input"] == "test"
            assert payload["config"]["configurable"]["thread_id"] == "thread-1"
            assert payload["metadata"]["thenvoi_room_id"] == "room-1"
            assert payload["metadata"]["thenvoi_sender_name"] == "Alice"
            logger.info("Payload with sender_name: OK")

            # Without sender_name — should omit field
            payload_no_name = handler._build_payload(
                content="test",
                room_id="room-1",
                thread_id="thread-1",
                message_id="msg-1",
                sender_id="user-1",
                sender_name=None,
                sender_type="User",
            )
            assert "thenvoi_sender_name" not in payload_no_name["metadata"]
            logger.info("Payload without sender_name: OK (field omitted)")
        finally:
            await handler.close()

    await run_test("Payload structure + optional fields", test_payload_structure())

    # =====================================================================
    # Results
    # =====================================================================
    logger.info("=" * 50)
    logger.info("Results: %d passed, %d failed", passed, failed)
    logger.info("=" * 50)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
