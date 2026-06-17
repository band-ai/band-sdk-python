"""E2E test helper functions.

Provides utilities for sending messages, waiting for agent responses,
and asserting on message content in E2E tests.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from band_rest import AsyncRestClient, ChatMessageRequest
from band_rest.types import (
    ChatMessageRequestMentionsItem as Mention,
)

from band.client.streaming import MessageCreatedPayload, WebSocketClient

logger = logging.getLogger(__name__)


# =============================================================================
# Pretty logging (followable transcript when running with -s)
# =============================================================================

# Rich renders a readable transcript under ``pytest -s`` and degrades to plain
# text when stdout is captured/non-tty. All dynamic text is passed through
# ``rich.text.Text`` (no markup parsing) so values like "[Milk $3.50]" can't
# be misread as style tags.
_console = Console()

# Style + icon per step kind. Numeric/other steps fall back to the default.
_STEP_KINDS: dict[str, tuple[str, str]] = {
    "assert": ("bold green", "✔"),
    "restart": ("bold yellow", "⟳"),
    "retry": ("bold dark_orange", "↻"),
}
_STEP_DEFAULT: tuple[str, str] = ("bold cyan", "▶")


def log_banner(title: str) -> None:
    """Render a boxed section banner; green when it announces a pass."""
    passed = "PASS" in title.upper()
    _console.print()
    _console.print(
        Panel(
            Text(title, style="bold green" if passed else "bold bright_white"),
            border_style="green" if passed else "bright_cyan",
            padding=(0, 2),
            expand=True,
        )
    )


def log_step(n: int | str | float, text: str) -> None:
    """Render a color/icon-coded step marker within a scenario."""
    style, icon = _STEP_KINDS.get(str(n), _STEP_DEFAULT)
    label = str(n) if str(n) in _STEP_KINDS else f"step {n}"
    line = Text("  ")
    line.append(f"{icon} {label}", style=style)
    line.append("  ")
    line.append(text, style="white")
    _console.print(line)


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


@asynccontextmanager
async def listening_for_room_activity(
    ws_client: WebSocketClient | TrackingWebSocketClient,
    room_id: str,
    *,
    timeout: float = 30.0,
    message_types: tuple[str, ...] = ("text",),
    sender_id: str | None = None,
    min_messages: int = 1,
    raise_on_timeout: bool = False,
) -> AsyncGenerator[Callable[[], Awaitable[list[MessageCreatedPayload]]], None]:
    """Subscribe to a room and collect agent activity matching a filter.

    A generalized variant of :func:`listening_for_agent_responses` that can
    capture non-text events (``thought``, ``tool_call``, ``tool_result``) and
    optionally restrict to a single sender. Collects every ``message_created``
    payload from an Agent whose ``message_type`` is in *message_types* (and,
    when *sender_id* is given, whose ``sender_id`` matches).

    Usage::

        async with listening_for_room_activity(
            ws, room_id, message_types=("thought",)
        ) as wait:
            await send_trigger_message(client, room_id, "Think it through", ...)
            thoughts = await wait()

    Args:
        ws_client: Connected WebSocket client (or TrackingWebSocketClient).
        room_id: Chat room to listen on.
        timeout: Maximum seconds ``wait()`` will block.
        message_types: Message types to collect (default text only).
        sender_id: If set, only collect activity from this sender.
        min_messages: Minimum matching messages before ``wait()`` returns.
        raise_on_timeout: If True, ``wait()`` raises ``TimeoutError`` instead
            of returning partial results.

    Yields:
        An async callable that blocks until *min_messages* matching messages
        arrive (or *timeout* elapses) and returns the collected payloads.
    """
    received: list[MessageCreatedPayload] = []
    event = asyncio.Event()

    async def handler(payload: MessageCreatedPayload) -> None:
        if payload.sender_type != "Agent" or payload.message_type not in message_types:
            return
        if sender_id is not None and payload.sender_id != sender_id:
            return
        received.append(payload)
        logger.info(
            "Received %s from %s in room %s: %s",
            payload.message_type,
            payload.sender_name or payload.sender_id,
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
                    "Timeout waiting for %s in room %s (received %d/%d after %.1fs)",
                    message_types,
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


def find_tool_call_in_context(items: list[Any], tool_name: str) -> bool:
    """Return True if any context item is a ``tool_call`` event for *tool_name*.

    The Agno adapter posts tool executions as ``tool_call`` events whose
    ``content`` is a JSON object ``{"name": ..., "args": ..., ...}`` (see
    ``AgnoAdapter._emit_execution``). This parses those payloads and matches
    on the tool name, falling back to a substring check if the content is not
    valid JSON.

    Args:
        items: Context items from ``fetch_all_context`` (each has
            ``message_type`` and ``content`` attributes).
        tool_name: The tool name to look for (e.g. ``"add_numbers"``).
    """
    matches = (
        _tool_call_name_matches(item, tool_name)
        for item in items
        if getattr(item, "message_type", None) == "tool_call"
    )
    return any(matches)


def _tool_call_name_matches(item: Any, tool_name: str) -> bool:
    """Check a single ``tool_call`` context item against *tool_name*."""
    content = getattr(item, "content", "") or ""
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return tool_name in content
    return isinstance(parsed, dict) and parsed.get("name") == tool_name


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
