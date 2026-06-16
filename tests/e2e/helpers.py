"""E2E test helper functions.

Provides utilities for sending messages, waiting for agent responses,
and asserting on message content in E2E tests.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from thenvoi_rest import AsyncRestClient, ChatMessageRequest
from thenvoi_rest.types import (
    ChatMessageRequestMentionsItem as Mention,
)

from thenvoi.client.streaming import MessageCreatedPayload, WebSocketClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiveToolObservation:
    """A live platform event proving one adapter tool execution phase."""

    event_id: str
    room_id: str | None
    agent_id: str | None
    message_type: str
    tool_name: str
    tool_call_id: str | None
    payload: dict[str, Any]


@dataclass(frozen=True)
class SuccessfulToolExecution:
    """A call/result pair correlated by tool_call_id and checked for errors."""

    tool_name: str
    tool_call_id: str
    call: LiveToolObservation
    result: LiveToolObservation


class ToolObservationUnavailableError(AssertionError):
    """Raised when the platform cannot expose live tool execution events."""


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
        """Leave all tracked channels and fail teardown if any leave fails."""
        failures: list[tuple[str, Exception]] = []
        for room_id in list(self._joined_rooms):
            try:
                await self._ws.leave_chat_room_channel(room_id)
            except Exception as exc:
                failures.append((room_id, exc))
            else:
                self._joined_rooms.discard(room_id)
        if failures:
            failed_rooms = ", ".join(room_id for room_id, _exc in failures)
            raise AssertionError(
                "Failed to leave E2E WebSocket room channel(s): "
                f"{failed_rooms}. Listener cleanup failures can leak events into "
                "later tests, so teardown is not best-effort."
            ) from failures[0][1]


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
        body_exc = sys.exc_info()[1]
        try:
            await ws_client.leave_chat_room_channel(room_id)
        except Exception as cleanup_exc:
            message = (
                f"Failed to leave E2E WebSocket room channel {room_id}. "
                "Listener cleanup failures can leak events into later tests."
            )
            if body_exc is not None:
                if hasattr(body_exc, "add_note"):
                    body_exc.add_note(f"{message} Cleanup error: {cleanup_exc!r}")
                logger.exception("%s Preserving primary test failure.", message)
            else:
                raise AssertionError(message) from cleanup_exc


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


async def fetch_agent_room_context(
    client: AsyncRestClient,
    room_id: str,
    *,
    page_size: int = 100,
    max_pages: int = 10,
) -> list[Any]:
    """Fetch the agent-visible room context (oldest first) from the platform.

    The context endpoint is the live proof surface for adapter execution events:
    it includes all messages/events sent by the agent, including ``tool_call``
    and ``tool_result`` events emitted by adapters with execution reporting.
    """
    context_client = getattr(client, "agent_api_context", None)
    getter = getattr(context_client, "get_agent_chat_context", None)
    if getter is None:
        raise ToolObservationUnavailableError(
            "tier2_blocked: agent context endpoint is unavailable for tool proof"
        )

    messages: list[Any] = []
    page = 1
    while page <= max_pages:
        try:
            response = await getter(room_id, page=page, page_size=page_size)
        except Exception as exc:
            raise ToolObservationUnavailableError(
                "tier2_blocked: failed to fetch live agent context for tool proof"
            ) from exc
        messages.extend(list(getattr(response, "data", None) or []))
        meta = getattr(response, "meta", None)
        total_pages = getattr(meta, "total_pages", None)
        if total_pages is not None:
            try:
                if page >= int(total_pages):
                    break
            except (TypeError, ValueError):
                break
        elif len(getattr(response, "data", None) or []) < page_size:
            break
        page += 1
    return messages


def _safe_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _metadata_dict(message: Any) -> dict[str, Any]:
    metadata = message_value(message, "metadata")
    if hasattr(metadata, "model_dump"):
        dumped = metadata.model_dump()
        return dumped if isinstance(dumped, dict) else {}
    return metadata if isinstance(metadata, dict) else {}


def _tool_payload(message: Any) -> dict[str, Any]:
    metadata = _metadata_dict(message)
    content_payload = _safe_json_object(message_value(message, "content"))
    payload = {**metadata, **content_payload}
    nested = payload.get("tool")
    if isinstance(nested, dict):
        payload = {**payload, **nested}
    return payload


def _tool_name_from_payload(payload: dict[str, Any]) -> str | None:
    for key in ("name", "tool_name", "tool", "function_name"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    function = payload.get("function")
    if isinstance(function, dict):
        value = function.get("name")
        if isinstance(value, str) and value:
            return value
    return None


def _room_id_from_message(message: Any) -> str | None:
    for key in ("chat_room_id", "room_id", "chat_id"):
        value = message_value(message, key)
        if value:
            return str(value)
    return None


def tool_observations_after_boundary(
    messages: list[Any],
    *,
    room_id: str,
    agent_id: str,
    after_message_id: str,
) -> list[LiveToolObservation]:
    """Extract tool call/result observations after a trigger message.

    ``messages`` must be the chronological agent context from
    ``fetch_agent_room_context``. The trigger message is the turn boundary, so
    observations before it cannot satisfy the current step's proof.
    """
    boundary_index = next(
        (
            index
            for index, message in enumerate(messages)
            if str(message_value(message, "id")) == str(after_message_id)
        ),
        None,
    )
    if boundary_index is None:
        raise ToolObservationUnavailableError(
            "tier2_blocked: trigger message is absent from agent context; "
            "cannot bound live tool observations"
        )

    observations: list[LiveToolObservation] = []
    for message in messages[boundary_index + 1 :]:
        message_type = str(message_value(message, "message_type") or "")
        if message_type not in {"tool_call", "tool_result"}:
            continue
        observed_room_id = _room_id_from_message(message)
        if observed_room_id is not None and observed_room_id != room_id:
            continue
        observed_agent_id = message_value(message, "sender_id")
        if observed_agent_id is not None and str(observed_agent_id) != agent_id:
            continue
        payload = _tool_payload(message)
        tool_name = _tool_name_from_payload(payload) or ""
        tool_call_id = payload.get("tool_call_id") or payload.get("id")
        observations.append(
            LiveToolObservation(
                event_id=str(message_value(message, "id")),
                room_id=observed_room_id,
                agent_id=str(observed_agent_id)
                if observed_agent_id is not None
                else None,
                message_type=message_type,
                tool_name=tool_name,
                tool_call_id=str(tool_call_id) if tool_call_id is not None else None,
                payload=payload,
            )
        )
    return observations


def _observation_summary(
    observations: list[LiveToolObservation],
) -> list[dict[str, str | None]]:
    return [
        {
            "event_id": observation.event_id,
            "message_type": observation.message_type,
            "tool_name": observation.tool_name,
            "tool_call_id": observation.tool_call_id,
        }
        for observation in observations
    ]


def assert_required_tool_observations(
    observations: list[LiveToolObservation],
    *,
    required_tool_names: set[str] | frozenset[str],
) -> None:
    """Require both a live ``tool_call`` and ``tool_result`` per tool name."""
    missing: dict[str, list[str]] = {}
    for tool_name in sorted(required_tool_names):
        observed_types = {
            observation.message_type
            for observation in observations
            if observation.tool_name == tool_name
        }
        missing_types = sorted({"tool_call", "tool_result"} - observed_types)
        if missing_types:
            missing[tool_name] = missing_types
    if missing:
        raise AssertionError(
            "Missing live platform tool execution observations: "
            f"{missing}; observed={_observation_summary(observations)}"
        )


def _tool_result_is_error(payload: dict[str, Any]) -> bool:
    if payload.get("is_error") is True or "error" in payload:
        return True
    output = payload.get("output")
    if output is None:
        output = payload.get("result")
    if isinstance(output, str):
        return output.startswith(("Error", "Invalid arguments", "Unknown tool"))
    return False


def require_successful_tool_execution(
    observations: list[LiveToolObservation],
    *,
    tool_name: str,
) -> SuccessfulToolExecution:
    """Return a correlated non-error call/result pair for one tool."""
    calls_by_id = {
        observation.tool_call_id: observation
        for observation in observations
        if observation.message_type == "tool_call"
        and observation.tool_name == tool_name
        and observation.tool_call_id
    }
    if not calls_by_id:
        raise AssertionError(
            f"Missing live platform tool_call observation for {tool_name}; "
            f"observed={_observation_summary(observations)}"
        )

    result_candidates = [
        observation
        for observation in observations
        if observation.message_type == "tool_result"
        and observation.tool_call_id in calls_by_id
        and (observation.tool_name in {"", tool_name})
    ]
    if not result_candidates:
        raise AssertionError(
            f"Missing live platform tool_result observation for {tool_name}; "
            f"observed={_observation_summary(observations)}"
        )

    error_results = [
        observation
        for observation in result_candidates
        if _tool_result_is_error(observation.payload)
    ]
    if error_results:
        raise AssertionError(
            f"Live platform tool_result observation for {tool_name} reported an error: "
            f"{_observation_summary(error_results)}"
        )

    result = result_candidates[0]
    tool_call_id = result.tool_call_id
    if tool_call_id is None:
        raise AssertionError(f"Correlated tool_result for {tool_name} has no call id")
    return SuccessfulToolExecution(
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        call=calls_by_id[tool_call_id],
        result=result,
    )


def require_successful_tool_executions(
    observations: list[LiveToolObservation],
    *,
    required_tool_names: set[str] | frozenset[str],
) -> dict[str, SuccessfulToolExecution]:
    return {
        tool_name: require_successful_tool_execution(
            observations,
            tool_name=tool_name,
        )
        for tool_name in sorted(required_tool_names)
    }


async def wait_for_required_tool_observations(
    client: AsyncRestClient,
    *,
    room_id: str,
    agent_id: str,
    after_message_id: str,
    required_tool_names: set[str] | frozenset[str],
    timeout: float,
) -> list[LiveToolObservation]:
    """Poll agent context until required live tool execution events are present."""
    deadline = asyncio.get_running_loop().time() + timeout
    last_observations: list[LiveToolObservation] = []
    last_unavailable: ToolObservationUnavailableError | None = None
    while asyncio.get_running_loop().time() < deadline:
        try:
            messages = await fetch_agent_room_context(client, room_id)
            last_observations = tool_observations_after_boundary(
                messages,
                room_id=room_id,
                agent_id=agent_id,
                after_message_id=after_message_id,
            )
            assert_required_tool_observations(
                last_observations,
                required_tool_names=required_tool_names,
            )
        except ToolObservationUnavailableError as exc:
            last_unavailable = exc
            await asyncio.sleep(0.5)
            continue
        except AssertionError:
            await asyncio.sleep(0.5)
            continue
        return last_observations

    if last_unavailable is not None and not last_observations:
        raise last_unavailable
    assert_required_tool_observations(
        last_observations,
        required_tool_names=required_tool_names,
    )
    return last_observations


async def wait_for_successful_tool_executions(
    client: AsyncRestClient,
    *,
    room_id: str,
    agent_id: str,
    after_message_id: str,
    required_tool_names: set[str] | frozenset[str],
    timeout: float,
) -> dict[str, SuccessfulToolExecution]:
    """Poll agent context until required live tool executions are non-error pairs."""
    deadline = asyncio.get_running_loop().time() + timeout
    last_observations: list[LiveToolObservation] = []
    last_unavailable: ToolObservationUnavailableError | None = None
    while asyncio.get_running_loop().time() < deadline:
        try:
            messages = await fetch_agent_room_context(client, room_id)
            last_observations = tool_observations_after_boundary(
                messages,
                room_id=room_id,
                agent_id=agent_id,
                after_message_id=after_message_id,
            )
            return require_successful_tool_executions(
                last_observations,
                required_tool_names=required_tool_names,
            )
        except ToolObservationUnavailableError as exc:
            last_unavailable = exc
            await asyncio.sleep(0.5)
            continue
        except AssertionError:
            await asyncio.sleep(0.5)
            continue

    if last_unavailable is not None and not last_observations:
        raise last_unavailable
    return require_successful_tool_executions(
        last_observations,
        required_tool_names=required_tool_names,
    )


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


async def wait_full_window_for_new_agent_text_messages(
    client: AsyncRestClient,
    room_id: str,
    agent_id: str,
    exclude_ids: set[str] | frozenset[str],
    *,
    timeout: float,
    page_size: int = 100,
    poll_interval: float = 0.5,
) -> list[Any]:
    """Observe the entire window and return new agent text messages.

    This is for exact-count live proofs: it intentionally does not return early
    when the expected count appears, because a late duplicate inside the window
    must still fail the caller's assertion.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    while True:
        await fetch_chat_messages(client, room_id, page_size=page_size)
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        await asyncio.sleep(min(poll_interval, remaining))

    messages = await fetch_chat_messages(client, room_id, page_size=page_size)
    return agent_text_messages(messages, agent_id, exclude_ids)


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
