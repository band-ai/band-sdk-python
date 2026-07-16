"""Integration tests for Claude SDK chat-based approval flow.

Tests the approval flow against the real Band platform using two agents:
- Agent 1 (primary): Runs ClaudeSDKAdapter with approval_mode
- Agent 2 (secondary): Sends messages to Agent 1 via REST API

Credentials are loaded from .env.test (via conftest_integration.py).

Run with:
    uv run pytest tests/integration/test_claude_sdk_approval.py -v -s --no-cov
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from band_rest import AsyncRestClient, ChatMessageRequest, ChatRoomRequest
from band_rest.types import (
    ChatMessageRequestMentionsItem as Mention,
    ParticipantRequest,
)

from band import Agent
from band.adapters.claude_sdk import ClaudeSDKAdapter
from band.client.streaming import MessageCreatedPayload, WebSocketClient
from tests.integration.conftest import (
    get_api_key,
    get_api_key_2,
    get_base_url,
    get_test_agent_id,
    get_test_agent_id_2,
    get_ws_url,
    requires_multi_agent,
)

logger = logging.getLogger(__name__)


def _get_claude_agent_id() -> str:
    """Return the primary agent ID, or skip if not configured."""
    agent_id = get_test_agent_id()
    if not agent_id:
        pytest.skip("TEST_AGENT_ID not set")
    return agent_id


def _get_claude_agent_key() -> str:
    """Return the primary agent API key, or skip if not configured."""
    api_key = get_api_key()
    if not api_key:
        pytest.skip("BAND_API_KEY not set")
    return api_key


def _get_sender_agent_id() -> str:
    """Return the secondary agent ID, or skip if not configured."""
    agent_id = get_test_agent_id_2()
    if not agent_id:
        pytest.skip("TEST_AGENT_ID_2 not set")
    return agent_id


def _get_sender_agent_key() -> str:
    """Return the secondary agent API key, or skip if not configured."""
    api_key = get_api_key_2()
    if not api_key:
        pytest.skip("BAND_API_KEY_2 not set")
    return api_key


# --- Fixtures --------------------------------------------------------------


@pytest.fixture
def claude_rest() -> AsyncRestClient:
    """REST client authenticated as the Claude SDK agent."""
    return AsyncRestClient(api_key=_get_claude_agent_key(), base_url=get_base_url())


@pytest.fixture
def sender_rest() -> AsyncRestClient:
    """REST client authenticated as the sender agent."""
    return AsyncRestClient(api_key=_get_sender_agent_key(), base_url=get_base_url())


@pytest.fixture
async def approval_shared_room(claude_rest, sender_rest) -> str:
    """Get or create a chat room with both agents."""
    sender_agent_id = _get_sender_agent_id()

    # List claude agent's rooms
    response = await claude_rest.agent_api_chats.list_agent_chats()
    rooms = response.data or []

    # Find a room that has the sender agent
    for room in rooms:
        participants_resp = (
            await claude_rest.agent_api_participants.list_agent_chat_participants(
                room.id
            )
        )
        participant_ids = {p.id for p in (participants_resp.data or [])}
        if sender_agent_id in participant_ids:
            logger.info("Reusing existing room: %s", room.id)
            return room.id

    # Create new room and add sender
    create_resp = await claude_rest.agent_api_chats.create_agent_chat(
        chat=ChatRoomRequest()
    )
    room_id = create_resp.data.id
    logger.info("Created new room: %s", room_id)

    await claude_rest.agent_api_participants.add_agent_chat_participant(
        room_id,
        participant=ParticipantRequest(participant_id=sender_agent_id, role="member"),
    )
    logger.info("Added sender agent to room")

    return room_id


# --- Helpers ---------------------------------------------------------------


async def _wait_for_agent_message(
    ws: WebSocketClient,
    chat_id: str,
    timeout: float = 30.0,
    predicate=None,
) -> list[MessageCreatedPayload]:
    """Subscribe to a room and collect messages until predicate or timeout."""
    messages: list[MessageCreatedPayload] = []
    done = asyncio.Event()

    async def on_message(payload: MessageCreatedPayload):
        logger.info(
            "  [WS] Received message from %s: %s",
            payload.sender_name,
            (payload.content or "")[:120],
        )
        messages.append(payload)
        if predicate and predicate(payload):
            done.set()

    await ws.join_chat_room_channel(chat_id, on_message)
    await asyncio.sleep(0.3)  # Let subscription settle

    try:
        await asyncio.wait_for(done.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("Timed out waiting for message (collected %s)", len(messages))

    return messages


# --- Tests -----------------------------------------------------------------


@requires_multi_agent
class TestClaudeSDKApprovalIntegration:
    """Live integration tests for the chat-based approval flow."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(90)
    async def test_status_command_response(self, sender_rest, approval_shared_room):
        """Start agent with approval_mode='manual', send /status, verify response.

        This tests command interception end-to-end without needing Claude
        to trigger any tool use.
        """
        claude_agent_id = _get_claude_agent_id()
        claude_agent_key = _get_claude_agent_key()
        sender_agent_id = _get_sender_agent_id()
        sender_agent_key = _get_sender_agent_key()

        room_id = approval_shared_room
        logger.info("=== Test: /status command (room %s) ===", room_id)

        # Create adapter with manual approval mode
        adapter = ClaudeSDKAdapter(
            model="claude-sonnet-4-5-20250929",
            approval_mode="manual",
            custom_section="You are a test bot. Be concise.",
        )

        # Create agent
        agent = Agent.create(
            adapter=adapter,
            agent_id=claude_agent_id,
            api_key=claude_agent_key,
            ws_url=get_ws_url(),
            rest_url=get_base_url(),
        )

        # Subscribe to room for responses (as the sender agent)
        ws = WebSocketClient(
            ws_url=get_ws_url(),
            api_key=sender_agent_key,
            agent_id=sender_agent_id,
        )

        agent_task = None
        try:
            # Start the agent in background
            agent_task = asyncio.create_task(agent.run())
            await asyncio.sleep(3)  # Let agent connect + join rooms

            # Connect WebSocket as sender to watch for responses
            async with ws:
                messages: list[MessageCreatedPayload] = []
                response_received = asyncio.Event()

                async def on_message(payload: MessageCreatedPayload):
                    logger.info(
                        "  [WS] %s: %s",
                        payload.sender_name,
                        (payload.content or "")[:200],
                    )
                    # Look for a message from the Claude agent (not our own)
                    if payload.sender_id == claude_agent_id:
                        messages.append(payload)
                        if payload.content and "Claude SDK Status" in payload.content:
                            response_received.set()

                await ws.join_chat_room_channel(room_id, on_message)
                await asyncio.sleep(0.5)

                # Get Claude agent info for mention
                claude_info = await sender_rest.agent_api_peers.list_agent_peers()
                claude_peer = next(
                    (p for p in (claude_info.data or []) if p.id == claude_agent_id),
                    None,
                )
                claude_name = claude_peer.name if claude_peer else "Claude Agent"

                # Send /status command from sender agent
                logger.info("Sending /status command...")
                await sender_rest.agent_api_messages.create_agent_chat_message(
                    room_id,
                    message=ChatMessageRequest(
                        content="/status",
                        mentions=[Mention(id=claude_agent_id, name=claude_name)],
                    ),
                )

                # Wait for agent's response
                try:
                    await asyncio.wait_for(response_received.wait(), timeout=15.0)
                except asyncio.TimeoutError:
                    pass

                # Verify
                status_msgs = [
                    m
                    for m in messages
                    if m.content and "Claude SDK Status" in m.content
                ]

                # Log all collected messages for debugging
                logger.info("=== All collected messages (%s) ===", len(messages))
                for i, m in enumerate(messages):
                    logger.info(
                        "  [%d] sender=%s (%s), type=%s, content=%s",
                        i,
                        m.sender_name,
                        m.sender_id,
                        m.message_type,
                        (m.content or "")[:300],
                    )

                if status_msgs:
                    status_text = status_msgs[0].content
                    logger.info("=== Status response received ===")
                    logger.info(status_text)
                    assert "manual" in status_text, "Should show approval_mode=manual"
                    assert "model" in status_text, "Should show model info"
                    logger.info("PASS: /status command works correctly")
                else:
                    pytest.fail(
                        f"Agent did not respond with status. Got {len(messages)} messages."
                    )

        finally:
            # Graceful shutdown
            if agent_task and not agent_task.done():
                logger.info("Stopping agent...")
                agent_task.cancel()
                try:
                    await agent_task
                except (asyncio.CancelledError, Exception):
                    pass

    @pytest.mark.asyncio
    @pytest.mark.timeout(120)
    async def test_auto_accept_sends_notification(
        self, sender_rest, approval_shared_room
    ):
        """Start agent with auto_accept, send a message that triggers tool use,
        verify the approval notification appears in chat.

        This is a full end-to-end test of the auto_accept approval flow.
        """
        claude_agent_id = _get_claude_agent_id()
        claude_agent_key = _get_claude_agent_key()
        sender_agent_id = _get_sender_agent_id()
        sender_agent_key = _get_sender_agent_key()

        room_id = approval_shared_room
        logger.info("=== Test: auto_accept notification (room %s) ===", room_id)

        adapter = ClaudeSDKAdapter(
            model="claude-sonnet-4-5-20250929",
            approval_mode="auto_accept",
            approval_text_notifications=True,
            custom_section=(
                "You are a coding assistant. When asked to do something, "
                "always use the Bash tool to run a command first."
            ),
        )

        agent = Agent.create(
            adapter=adapter,
            agent_id=claude_agent_id,
            api_key=claude_agent_key,
            ws_url=get_ws_url(),
            rest_url=get_base_url(),
        )

        ws = WebSocketClient(
            ws_url=get_ws_url(),
            api_key=sender_agent_key,
            agent_id=sender_agent_id,
        )

        agent_task = None
        try:
            agent_task = asyncio.create_task(agent.run())
            await asyncio.sleep(3)

            async with ws:
                messages: list[MessageCreatedPayload] = []
                approval_seen = asyncio.Event()

                async def on_message(payload: MessageCreatedPayload):
                    logger.info(
                        "  [WS] %s (%s): %s",
                        payload.sender_name,
                        payload.message_type,
                        (payload.content or "")[:200],
                    )
                    if payload.sender_id == claude_agent_id:
                        messages.append(payload)
                        if payload.content and "accept" in payload.content.lower():
                            approval_seen.set()

                await ws.join_chat_room_channel(room_id, on_message)
                await asyncio.sleep(0.5)

                # Get Claude agent info for mention
                claude_info = await sender_rest.agent_api_peers.list_agent_peers()
                claude_peer = next(
                    (p for p in (claude_info.data or []) if p.id == claude_agent_id),
                    None,
                )
                claude_name = claude_peer.name if claude_peer else "Claude Agent"

                # Send a message that should trigger tool use
                logger.info("Sending message to trigger tool use...")
                await sender_rest.agent_api_messages.create_agent_chat_message(
                    room_id,
                    message=ChatMessageRequest(
                        content="Please run `echo hello` in the terminal",
                        mentions=[Mention(id=claude_agent_id, name=claude_name)],
                    ),
                )

                # Wait for approval notification
                try:
                    await asyncio.wait_for(approval_seen.wait(), timeout=60.0)
                except asyncio.TimeoutError:
                    pass

                # Check results
                approval_msgs = [
                    m for m in messages if m.content and "accept" in m.content.lower()
                ]

                if approval_msgs:
                    logger.info("=== Approval notification received ===")
                    logger.info(approval_msgs[0].content)
                    logger.info("PASS: auto_accept mode sends notification correctly")
                else:
                    logger.info(
                        "Collected %s messages from agent (no approval notification):",
                        len(messages),
                    )
                    for m in messages:
                        logger.info(
                            "  - [%s] %s", m.message_type, (m.content or "")[:150]
                        )
                    pytest.fail(
                        f"No approval notification received. "
                        f"Got {len(messages)} messages from agent but none "
                        f"contained 'accept'."
                    )

        finally:
            if agent_task and not agent_task.done():
                logger.info("Stopping agent...")
                agent_task.cancel()
                try:
                    await agent_task
                except (asyncio.CancelledError, Exception):
                    pass
