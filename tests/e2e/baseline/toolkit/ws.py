"""WebSocket observer wrapper for the baseline toolkit.

``TrackingWebSocketClient`` wraps a ``WebSocketClient`` and remembers the rooms
it has joined so they can all be left on teardown. Only the methods the toolkit
uses are explicitly delegated — no ``__getattr__`` proxy — so typos surface as
type-checker errors instead of silent runtime failures.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from band.client.streaming import MessageCreatedPayload, WebSocketClient

logger = logging.getLogger(__name__)


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
