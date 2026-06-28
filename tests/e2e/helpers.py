"""E2E test helper functions.

Provides utilities for sending messages, waiting for agent responses,
and asserting on message content in E2E tests.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

from band_rest import AsyncRestClient, ChatMessageRequest
from band_rest.types import (
    ChatMessageRequestMentionsItem as Mention,
)

from band.client.streaming import MessageCreatedPayload, WebSocketClient

logger = logging.getLogger(__name__)


class TrackingWebSocketClient:
    """Wrapper around WebSocketClient that tracks joined rooms for cleanup.

    Uses a set to avoid duplicate leave calls when tests manually leave
    and rejoin the same room. Only the methods used in E2E tests are
    explicitly delegated — no ``__getattr__`` proxy — so typos are caught
    by the type checker instead of failing silently at runtime.
    """

    def __init__(self, ws: WebSocketClient) -> None:
        self._ws = ws
        self._joined_rooms: set[str] = set()

    @property
    def ws(self) -> WebSocketClient:
        """Access the underlying WebSocketClient for methods not wrapped here."""
        return self._ws

    async def join_chat_room_channel(
        self,
        chat_room_id: str,
        on_message_created: Callable[[MessageCreatedPayload], Awaitable[None]],
    ):
        result = await self._ws.join_chat_room_channel(chat_room_id, on_message_created)
        self._joined_rooms.add(chat_room_id)
        return result

    async def leave_chat_room_channel(self, chat_room_id: str):
        result = await self._ws.leave_chat_room_channel(chat_room_id)
        self._joined_rooms.discard(chat_room_id)
        return result

    async def cleanup_channels(self) -> None:
        """Leave all tracked channels. Best-effort, errors are logged."""
        for room_id in list(self._joined_rooms):
            try:
                await self._ws.leave_chat_room_channel(room_id)
            except Exception:
                logger.debug("Failed to leave room %s during cleanup", room_id)
        self._joined_rooms.clear()


async def send_trigger_message(
    client: AsyncRestClient,
    room_id: str,
    content: str,
    mention_name: str,
    mention_id: str,
) -> str:
    """Send a message from the User that triggers the agent's processing loop.

    Uses **user** API credentials so the sender is the User, not the agent.
    The agent's runtime skips self-authored messages, so the trigger must
    come from a different participant.  The @mention targets the agent,
    satisfying both the platform's "at least one mention" requirement and
    ensuring the agent's preprocessor delivers the message.

    Args:
        client: REST API client (**user** credentials).
        room_id: Chat room to send the message in.
        content: Message content.
        mention_name: Name of the agent to @mention (trigger target).
        mention_id: ID of the agent to @mention (trigger target).

    Returns:
        The message ID of the sent message.
    """
    message_content = f"@{mention_name} {content}"
    response = await client.human_api_messages.send_my_chat_message(
        room_id,
        message=ChatMessageRequest(
            content=message_content,
            mentions=[Mention(id=mention_id, name=mention_name)],
        ),
    )
    message_id = response.data.id
    logger.info("Sent message %s to room %s: %s", message_id, room_id, content[:80])
    return message_id


@asynccontextmanager
async def listening_for_agent_responses(
    ws_client: WebSocketClient | TrackingWebSocketClient,
    room_id: str,
    timeout: float = 30.0,
    min_messages: int = 1,
    raise_on_timeout: bool = False,
) -> AsyncGenerator[Callable[[], Awaitable[list[MessageCreatedPayload]]], None]:
    """Context manager that subscribes to a room before any messages are sent.

    Subscribes to the chat room channel on entry, yields an async ``wait``
    function, and leaves the channel on exit.  Call ``wait()`` after sending
    a message to collect agent responses without a race condition.

    Usage::

        async with listening_for_agent_responses(ws, room_id) as wait:
            await send_trigger_message(client, room_id, "Hello", ...)
            received = await wait()

    Args:
        ws_client: Connected WebSocket client (or TrackingWebSocketClient).
        room_id: Chat room to listen on.
        timeout: Maximum seconds ``wait()`` will block.
        min_messages: Minimum agent messages to collect before returning.
        raise_on_timeout: If True, ``wait()`` raises ``TimeoutError`` instead
            of returning partial results.

    Yields:
        An async callable that blocks until *min_messages* agent messages
        arrive (or *timeout* elapses) and returns the collected messages.
    """
    received: list[MessageCreatedPayload] = []
    event = asyncio.Event()

    async def handler(payload: MessageCreatedPayload) -> None:
        if payload.sender_type == "Agent" and payload.message_type == "text":
            received.append(payload)
            logger.info(
                "Received agent response in room %s: %s",
                room_id,
                payload.content[:80],
            )
            if len(received) >= min_messages:
                event.set()

    await ws_client.join_chat_room_channel(room_id, handler)
    try:

        async def wait() -> list[MessageCreatedPayload]:
            try:
                await asyncio.wait_for(event.wait(), timeout=timeout)
            except TimeoutError:
                logger.warning(
                    "Timeout waiting for agent response in room %s "
                    "(received %d/%d messages after %.1fs)",
                    room_id,
                    len(received),
                    min_messages,
                    timeout,
                )
                if raise_on_timeout:
                    raise
            return received

        yield wait
    finally:
        await ws_client.leave_chat_room_channel(room_id)


def assert_content_contains(
    messages: list[MessageCreatedPayload],
    expected_substring: str,
) -> None:
    """Assert at least one message contains the expected substring.

    Args:
        messages: List of received messages to check.
        expected_substring: Substring that should appear in at least one message.

    Raises:
        AssertionError: If no message contains the expected substring.
    """
    contents = [m.content for m in messages]
    found = any(expected_substring.lower() in c.lower() for c in contents)
    assert found, (
        f"Expected at least one message to contain '{expected_substring}', "
        f"but got: {contents}"
    )


def assert_no_content_contains(
    messages: list[MessageCreatedPayload],
    unexpected_substring: str,
) -> None:
    """Assert no message contains the unexpected substring.

    Args:
        messages: List of received messages to check.
        unexpected_substring: Substring that should NOT appear in any message.

    Raises:
        AssertionError: If any message contains the unexpected substring.
    """
    contents = [m.content for m in messages]
    found = any(unexpected_substring.lower() in c.lower() for c in contents)
    assert not found, (
        f"Expected no message to contain '{unexpected_substring}', "
        f"but found it in: {contents}"
    )


# =============================================================================
# Shared Test Workflows
# =============================================================================


async def run_smoke_test(
    ws_client: TrackingWebSocketClient,
    api_client: AsyncRestClient,
    chat_id: str,
    agent_name: str,
    agent_id: str,
    timeout: float,
    adapter_name: str,
) -> list[MessageCreatedPayload]:
    """Run a smoke test: send a message and verify the agent responds.

    Args:
        api_client: User-scoped REST client (sends the trigger message).
        agent_name: Agent name for @mention (trigger target).
        agent_id: Agent ID for @mention (trigger target).

    Returns the list of received agent messages for further inspection.
    """
    async with listening_for_agent_responses(
        ws_client, chat_id, timeout=timeout
    ) as wait:
        await send_trigger_message(
            api_client, chat_id, "Say hello", agent_name, agent_id
        )
        received = await wait()

    assert len(received) > 0, (
        f"[{adapter_name}] Agent should have responded to the message"
    )
    logger.info(
        "[%s] Smoke test passed: received %d response(s)",
        adapter_name,
        len(received),
    )
    return received


async def run_tool_execution_test(
    ws_client: TrackingWebSocketClient,
    api_client: AsyncRestClient,
    chat_id: str,
    agent_name: str,
    agent_id: str,
    timeout: float,
    adapter_name: str,
) -> list[MessageCreatedPayload]:
    """Run a tool execution test: verify agent uses band_send_message.

    Args:
        api_client: User-scoped REST client (sends the trigger message).
        agent_name: Agent name for @mention (trigger target).
        agent_id: Agent ID for @mention (trigger target).

    Asks the agent to reply with a specific keyword (PINEAPPLE) and asserts
    it appears in the response. Returns the received messages.
    """
    async with listening_for_agent_responses(
        ws_client, chat_id, timeout=timeout
    ) as wait:
        await send_trigger_message(
            api_client,
            chat_id,
            "Reply with the word PINEAPPLE",
            agent_name,
            agent_id,
        )
        received = await wait()

    assert len(received) > 0, (
        f"[{adapter_name}] Agent should have sent a message via tool"
    )
    assert_content_contains(received, "PINEAPPLE")
    logger.info("[%s] Tool execution test passed", adapter_name)
    return received
