"""E2E test helper functions.

Provides utilities for sending messages, waiting for agent responses,
and asserting on message content in E2E tests.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any
from contextlib import asynccontextmanager

from thenvoi_rest import AsyncRestClient, ChatMessageRequest
from thenvoi_rest.types import (
    ChatMessageRequestMentionsItem as Mention,
)

from thenvoi.client.streaming import MessageCreatedPayload, WebSocketClient

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
        if chat_room_id in self._joined_rooms:
            await self.leave_chat_room_channel(chat_room_id)
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
    expected_agent_id: str | None = None,
    quiet_after_first: float = 0,
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
        expected_agent_id: Optional sender ID that responses must match. Use this
            when rooms may contain other active agents.

    Yields:
        An async callable that blocks until *min_messages* matching agent messages
        arrive (or *timeout* elapses) and returns the collected messages.
    """
    received: list[MessageCreatedPayload] = []
    event = asyncio.Event()

    async def handler(payload: MessageCreatedPayload) -> None:
        if payload.sender_type != "Agent" or payload.message_type != "text":
            return
        if expected_agent_id is not None and payload.sender_id != expected_agent_id:
            return

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
            if quiet_after_first:
                event.clear()
                try:
                    await asyncio.wait_for(event.wait(), timeout=quiet_after_first)
                except TimeoutError:
                    pass
            return received

        yield wait
    finally:
        try:
            await ws_client.leave_chat_room_channel(room_id)
        except Exception:
            logger.debug("Failed to leave room %s after listener exit", room_id)


def message_value(message: Any, key: str) -> Any:
    """Read a message field from REST or WebSocket message objects."""
    if isinstance(message, dict):
        return message.get(key)
    return getattr(message, key, None)


def message_ids(messages: list[Any]) -> set[str]:
    """Collect message IDs, for snapshotting a turn boundary in a room."""
    return {str(message_value(message, "id")) for message in messages}


def agent_text_messages(
    messages: list[Any],
    agent_id: str,
    exclude_ids: set[str] | frozenset[str] = frozenset(),
) -> list[Any]:
    """Filter to text messages sent by *agent_id*, excluding prior-turn IDs."""
    return [
        message
        for message in messages
        if message_value(message, "sender_id") == agent_id
        and message_value(message, "message_type") == "text"
        and str(message_value(message, "id")) not in exclude_ids
    ]


def mention_ids(message: Any) -> set[str]:
    """Extract the participant IDs carried in a message's mention metadata."""
    metadata = message_value(message, "metadata")
    mentions = message_value(metadata, "mentions") or []
    return {str(message_value(mention, "id")) for mention in mentions}


async def fetch_chat_messages(
    client: AsyncRestClient,
    room_id: str,
    page_size: int = 100,
) -> list[Any]:
    """Fetch the current durable message log for a room (newest first)."""
    response = await client.human_api_messages.list_my_chat_messages(
        room_id,
        page_size=page_size,
    )
    return list(response.data or [])


async def participant_ids(client: AsyncRestClient, chat_id: str) -> set[str]:
    """Fetch participant IDs currently present in a room."""
    participants = await client.human_api_participants.list_my_chat_participants(
        chat_id
    )
    return {participant.id for participant in (participants.data or [])}


async def wait_until_participant_present(
    client: AsyncRestClient,
    chat_id: str,
    participant_id: str,
    timeout: float,
) -> None:
    """Poll until a participant has joined a room."""
    deadline = asyncio.get_running_loop().time() + timeout
    last_ids: set[str] = set()
    while asyncio.get_running_loop().time() < deadline:
        last_ids = await participant_ids(client, chat_id)
        if participant_id in last_ids:
            return
        await asyncio.sleep(0.5)
    raise AssertionError(f"Participant {participant_id} never joined: {last_ids}")


async def wait_until_participant_absent(
    client: AsyncRestClient,
    chat_id: str,
    participant_id: str,
    timeout: float,
) -> None:
    """Poll until a participant has left a room."""
    deadline = asyncio.get_running_loop().time() + timeout
    last_ids: set[str] = set()
    while asyncio.get_running_loop().time() < deadline:
        last_ids = await participant_ids(client, chat_id)
        if participant_id not in last_ids:
            return
        await asyncio.sleep(0.5)
    raise AssertionError(f"Participant {participant_id} remained present: {last_ids}")


async def wait_for_new_agent_text_messages(
    client: AsyncRestClient,
    room_id: str,
    agent_id: str,
    exclude_ids: set[str] | frozenset[str],
    *,
    min_count: int = 1,
    timeout: float,
    quiet_after: float = 0,
    page_size: int = 100,
) -> list[Any]:
    """Wait for new text messages from an agent after a room snapshot.

    When ``quiet_after`` is set, the helper keeps polling after ``min_count`` is
    reached and returns only after no additional matching messages appear during
    that quiet window. This lets live tests assert exact per-turn reply counts
    without relying on substring matching or WebSocket timing.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    quiet_deadline: float | None = None
    last_count = 0
    last_agent_messages: list[Any] = []

    while asyncio.get_running_loop().time() < deadline:
        messages = await fetch_chat_messages(client, room_id, page_size=page_size)
        current = agent_text_messages(messages, agent_id, exclude_ids)
        if len(current) >= min_count:
            if quiet_after <= 0:
                return current
            now = asyncio.get_running_loop().time()
            if len(current) != last_count:
                last_count = len(current)
                quiet_deadline = now + quiet_after
                last_agent_messages = current
            elif quiet_deadline is not None and now >= quiet_deadline:
                return current
        else:
            last_agent_messages = current
        await asyncio.sleep(0.5)

    summary = [
        {
            "id": message_value(message, "id"),
            "content": str(message_value(message, "content") or "")[:160],
        }
        for message in last_agent_messages[:12]
    ]
    raise TimeoutError(
        f"Timed out waiting for {min_count} new text message(s) from {agent_id}: "
        f"{summary}"
    )


async def wait_for_chat_messages(
    client: AsyncRestClient,
    room_id: str,
    predicate: Callable[[list[Any]], bool],
    timeout: float,
) -> list[Any]:
    """Poll durable room history until the expected live E2E state appears."""
    deadline = asyncio.get_running_loop().time() + timeout
    last_messages: list[Any] = []
    while asyncio.get_running_loop().time() < deadline:
        last_messages = await fetch_chat_messages(client, room_id, page_size=50)
        if predicate(last_messages):
            return last_messages
        await asyncio.sleep(0.5)

    summary = [
        {
            "type": message_value(message, "message_type"),
            "sender_id": message_value(message, "sender_id"),
            "sender": message_value(message, "sender_name"),
            "content": str(message_value(message, "content") or "")[:160],
        }
        for message in last_messages[:12]
    ]
    raise TimeoutError(f"Timed out waiting for expected chat messages: {summary}")


def assert_content_contains(
    messages: list[Any],
    expected_substring: str,
) -> None:
    """Assert at least one message contains the expected substring.

    Args:
        messages: List of received messages to check.
        expected_substring: Substring that should appear in at least one message.

    Raises:
        AssertionError: If no message contains the expected substring.
    """
    contents = [str(message_value(m, "content") or "") for m in messages]
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
    contents = [str(message_value(m, "content") or "") for m in messages]
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
        ws_client,
        chat_id,
        timeout=timeout,
        expected_agent_id=agent_id,
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
    """Run a tool execution test: verify agent uses thenvoi_send_message.

    Args:
        api_client: User-scoped REST client (sends the trigger message).
        agent_name: Agent name for @mention (trigger target).
        agent_id: Agent ID for @mention (trigger target).

    Asks the agent to reply with a specific keyword (PINEAPPLE) and asserts
    it appears in the response. Returns the received messages.
    """
    async with listening_for_agent_responses(
        ws_client,
        chat_id,
        timeout=timeout,
        expected_agent_id=agent_id,
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
