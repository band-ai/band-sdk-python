"""HTTP callback server for receiving Kore.ai bot responses."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from typing import Any

from aiohttp import web

from thenvoi.integrations.koreai.template_extractor import extract_text
from thenvoi.integrations.koreai.types import CallbackData

logger = logging.getLogger(__name__)


class KoreAICallbackServer:
    """HTTP server that receives async callback POSTs from Kore.ai.

    Each outbound message registers a ``_TurnCollector`` keyed by room_id.
    Kore.ai may send multiple callbacks for a single turn (message + task
    completion). The collector accumulates them until a task-completion
    callback arrives or the caller times out.

    Routing: With per-room serialization the adapter guarantees at most one
    inflight turn per room, but multiple rooms can have inflight turns
    concurrently. Because Kore.ai callbacks do not include the room_id,
    the server tracks which room_id is currently expecting a response via
    ``_active_room``. Only one room can be active at a time -- the adapter
    must acquire ``active_room_lock`` before registering a turn.
    """

    def __init__(
        self,
        *,
        bind_host: str = "127.0.0.1",
        port: int = 3100,
        webhook_secret: str | None = None,
    ) -> None:
        self._bind_host = bind_host
        self._port = port
        self._webhook_secret = webhook_secret
        self._app = web.Application()
        self._app.router.add_post("/koreai/callback", self._handle_callback)
        self._runner: web.AppRunner | None = None
        self._collectors: dict[str, _TurnCollector] = {}

        # Because Kore.ai callbacks don't include the room_id, only one
        # room can have an inflight turn at a time to avoid routing
        # callbacks to the wrong room.
        self._active_room: str | None = None
        self.active_room_lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the HTTP server."""
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._bind_host, self._port)
        await site.start()
        logger.info(
            "Kore.ai callback server listening on %s:%d",
            self._bind_host,
            self._port,
        )

    async def stop(self) -> None:
        """Stop the HTTP server."""
        if self._runner:
            await self._runner.cleanup()
            self._runner = None

        # Cancel any pending collectors
        for collector in self._collectors.values():
            collector.finish()
        self._collectors.clear()
        self._active_room = None

    def register_turn(self, room_id: str) -> _TurnCollector:
        """Register a pending turn for a room.

        The caller MUST hold ``active_room_lock`` before calling this.

        Returns a collector that accumulates callback data until the turn
        is complete (task-completion callback or timeout).
        """
        # Cancel any existing collector for this room (shouldn't happen
        # with per-room serialization, but be safe)
        existing = self._collectors.pop(room_id, None)
        if existing is not None:
            existing.finish()

        collector = _TurnCollector(room_id)
        self._collectors[room_id] = collector
        self._active_room = room_id
        return collector

    def unregister_turn(self, room_id: str) -> None:
        """Remove the pending turn collector for a room."""
        collector = self._collectors.pop(room_id, None)
        if collector is not None:
            collector.finish()
        if self._active_room == room_id:
            self._active_room = None

    async def _handle_callback(self, request: web.Request) -> web.Response:
        """Handle an incoming callback POST from Kore.ai."""
        # Validate webhook secret if configured. KoreAIAdapter currently
        # rejects webhook_secret because the Kore.ai callback signing contract
        # has not been validated; this branch is kept only for direct server
        # tests or a future adapter revision that wires in verified headers.
        if self._webhook_secret:
            body_bytes = await request.read()
            if not self._verify_signature(request, body_bytes):
                logger.warning("Callback rejected: invalid signature")
                return web.Response(status=401, text="Invalid signature")
            try:
                body = json.loads(body_bytes)
            except (json.JSONDecodeError, ValueError):
                logger.warning("Callback rejected: invalid JSON body")
                return web.Response(status=400, text="Invalid JSON")
        else:
            try:
                body = await request.json()
            except (json.JSONDecodeError, Exception):
                logger.warning("Callback rejected: invalid JSON body")
                return web.Response(status=400, text="Invalid JSON")

        logger.debug("Received callback: %s", json.dumps(body)[:500])

        # Route to the active room's collector
        self._dispatch_callback(body)

        return web.Response(status=200, text="OK")

    def _dispatch_callback(self, body: dict[str, Any]) -> None:
        """Dispatch a callback to the active turn collector."""
        active_room = self._active_room
        if active_room is None:
            logger.warning("Received callback but no active turn, discarding")
            return

        collector = self._collectors.get(active_room)
        if collector is None:
            logger.warning(
                "Received callback for room %s but collector is gone, discarding",
                active_room,
            )
            return

        is_task_completion = "endOfTask" in body

        if is_task_completion:
            end_reason = body.get("endReason", "")
            task_name = body.get("completedTaskName", "")
            is_transfer = end_reason == "Interrupted"
            collector.add_task_completion(
                end_reason=end_reason,
                task_name=task_name,
                is_agent_transfer=is_transfer,
            )
            return

        # Message callback
        text_field = body.get("text")
        if text_field is not None:
            extracted = extract_text(text_field)
            if extracted:
                collector.add_message(extracted)
            return

        logger.debug("Callback has neither 'text' nor 'endOfTask', discarding")

    def _verify_signature(self, request: web.Request, body_bytes: bytes) -> bool:
        """Verify HMAC signature on the callback request."""
        signature = request.headers.get("X-Kore-Signature", "")
        if not signature:
            return False

        expected = hmac.new(
            self._webhook_secret.encode("utf-8") if self._webhook_secret else b"",
            body_bytes,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(signature, expected)


class _TurnCollector:
    """Collects callback data for a single adapter turn (one outbound message).

    A turn may receive multiple callbacks: one or more message callbacks,
    then optionally a task-completion callback.
    """

    def __init__(self, room_id: str) -> None:
        self.room_id = room_id
        self._data = CallbackData()
        self._message_event = asyncio.Event()
        self._done_event = asyncio.Event()

    @property
    def data(self) -> CallbackData:
        """The accumulated callback data."""
        return self._data

    def add_message(self, text: str) -> None:
        """Add a message callback."""
        self._data.messages.append(text)
        self._message_event.set()

    def add_task_completion(
        self,
        *,
        end_reason: str,
        task_name: str,
        is_agent_transfer: bool,
    ) -> None:
        """Add a task-completion callback and mark the turn as done."""
        self._data.task_completed = True
        self._data.end_reason = end_reason
        self._data.task_name = task_name
        self._data.is_agent_transfer = is_agent_transfer
        self._done_event.set()

    def finish(self) -> None:
        """Mark the turn as done (e.g. on timeout or cancellation)."""
        self._done_event.set()

    async def wait_for_messages(self, timeout: float) -> CallbackData:
        """Wait for callbacks up to ``timeout`` seconds.

        Returns collected data when either:
        - A task-completion callback is received, or
        - The timeout expires.

        Messages that arrive before task completion are accumulated.
        """
        try:
            await asyncio.wait_for(self._done_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "Kore.ai callback timeout after %ss for room %s",
                timeout,
                self.room_id,
            )
        return self._data

    async def wait_for_first_message(self, timeout: float) -> bool:
        """Wait for at least one message callback.

        Returns True if a message was received, False on timeout.
        """
        try:
            await asyncio.wait_for(self._message_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False
