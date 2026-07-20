"""WebSocket observer wrapper for the baseline toolkit.

``TrackingWebSocketClient`` wraps a ``WebSocketClient`` and remembers the rooms
it has joined so they can all be left on teardown. Only the methods the toolkit
uses are explicitly delegated — no ``__getattr__`` proxy — so typos surface as
type-checker errors instead of silent runtime failures.

``user_ws_observer`` is the one construction of the user-authenticated
observer connection, shared by the pytest fixture (``baseline_ws``) and
pytest-free callers (e.g. the sandbox staging smoke's ``probe.py``).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from band.client.streaming import MessageCreatedPayload, WebSocketClient

from tests.e2e.baseline.settings import BaselineSettings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def user_ws_observer(
    settings: BaselineSettings,
) -> AsyncIterator[TrackingWebSocketClient]:
    """Connect a user-authenticated WS observer; leave its channels on exit.

    Connects as the user (not an agent), so it coexists with agents and
    receives the same ``message_created`` events.
    """
    if not settings.credentials.api_key_user:
        raise ValueError("BAND_API_KEY_USER is required for the WS observer")
    ws = WebSocketClient(
        ws_url=settings.endpoints.ws_url,
        api_key=settings.credentials.api_key_user,
        agent_id=None,  # user connection, not an agent
    )
    async with ws, TrackingWebSocketClient(ws) as tracking:
        yield tracking


class TrackingWebSocketClient:
    """Async-context-manager wrapper that tracks joined rooms and leaves them on exit.

    Use as ``async with TrackingWebSocketClient(ws) as client:`` — every room it
    joins is left on exit, so callers never hand-roll a ``try/finally``.
    ``cleanup_channels`` remains public for callers that manage the lifecycle
    themselves. Uses a set to avoid duplicate leave calls when tests manually
    leave and rejoin the same room. Only the methods used in E2E tests are
    explicitly delegated — no ``__getattr__`` proxy — so typos are caught by the
    type checker instead of failing silently at runtime.
    """

    def __init__(self, ws: WebSocketClient) -> None:
        self._ws = ws
        self._joined_rooms: set[str] = set()

    async def __aenter__(self) -> TrackingWebSocketClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.cleanup_channels()

    @property
    def ws(self) -> WebSocketClient:
        """Access the underlying WebSocketClient for methods not wrapped here."""
        return self._ws

    async def join_chat_room_channel(
        self,
        chat_room_id: str,
        on_message_created: Callable[[MessageCreatedPayload], Awaitable[None]],
        on_message_updated: Callable[[MessageCreatedPayload], Awaitable[None]]
        | None = None,
    ) -> object:
        result = await self._ws.join_chat_room_channel(
            chat_room_id, on_message_created, on_message_updated
        )
        self._joined_rooms.add(chat_room_id)
        return result

    async def leave_chat_room_channel(self, chat_room_id: str) -> object:
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
