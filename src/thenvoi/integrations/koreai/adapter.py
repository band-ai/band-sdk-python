"""Experimental Kore.ai XO Platform adapter.

Bridges Thenvoi's WebSocket-based agent model and Kore.ai's HTTP webhook
model. Maintains a persistent connection to Thenvoi and runs an HTTP
callback server for Kore.ai bot responses.

One adapter instance corresponds to one Kore.ai bot. This adapter is marked
experimental until the inbound Kore.ai callback authentication contract is
validated against a live Kore.ai tenant.
"""

from __future__ import annotations

import asyncio
import logging
import time

from thenvoi.core.protocols import AgentToolsProtocol
from thenvoi.core.simple_adapter import SimpleAdapter
from thenvoi.core.types import AdapterFeatures, PlatformMessage
from thenvoi.converters.koreai import KoreAIHistoryConverter
from thenvoi.integrations.koreai.callback_server import KoreAICallbackServer
from thenvoi.integrations.koreai.client import KoreAIClient
from thenvoi.integrations.koreai.types import (
    CallbackData,
    KoreAIConfig,
    KoreAIRoomState,
    KoreAISessionState,
)

logger = logging.getLogger(__name__)


class KoreAIAdapter(SimpleAdapter[KoreAISessionState]):
    """Adapter that bridges Thenvoi and Kore.ai XO Platform.

    This adapter does not invoke an LLM. It forwards messages from Thenvoi
    chat rooms to a Kore.ai webhook bot and delivers bot responses back to
    the chat room.

    Each Thenvoi room maps to an independent Kore.ai conversation session
    via the ``from.id`` field (set to the room ID).

    Args:
        config: Kore.ai configuration (credentials, endpoints, timeouts).
        custom_section: Optional text appended to the agent description.
        features: Adapter feature flags.
    """

    def __init__(
        self,
        config: KoreAIConfig,
        *,
        custom_section: str = "",
        features: AdapterFeatures | None = None,
    ) -> None:
        super().__init__(
            history_converter=KoreAIHistoryConverter(),
            features=features,
        )
        self.config = config
        self.custom_section = custom_section
        self._client: KoreAIClient | None = None
        self._callback_server: KoreAICallbackServer | None = None

        # Per-room state
        self._room_states: dict[str, KoreAIRoomState] = {}
        self._room_locks: dict[str, asyncio.Lock] = {}

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Initialize the adapter: start callback server and HTTP client."""
        await super().on_started(agent_name, agent_description)

        # Warn about security configuration. The callback signature contract
        # has not been validated against Kore.ai's live callback delivery, so
        # this adapter remains experimental and must not claim production
        # webhook authentication yet.
        if self.config.webhook_secret:
            raise ValueError(
                "Kore.ai callback signature verification is not implemented: "
                "webhook_secret was configured, but Kore.ai's callback signing "
                "contract has not been validated for this adapter."
            )
        logger.warning(
            "KoreAIAdapter is experimental: inbound callback authentication "
            "has not been validated, so the callback server accepts requests "
            "without cryptographic verification."
        )
        if self.config.callback_url.startswith("http://"):
            logger.warning(
                "callback_url uses HTTP. Use HTTPS in production with a "
                "reverse proxy for TLS termination."
            )

        # Start callback server
        self._callback_server = KoreAICallbackServer(
            bind_host=self.config.callback_bind_host,
            port=self.config.callback_port,
            webhook_secret=self.config.webhook_secret,
        )
        await self._callback_server.start()

        # Start HTTP client
        self._client = KoreAIClient(self.config)
        await self._client.start()

        logger.info(
            "KoreAIAdapter started: bot_id=%s, callback=%s:%d",
            self.config.bot_id,
            self.config.callback_bind_host,
            self.config.callback_port,
        )

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: KoreAISessionState,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        """Forward a message to Kore.ai and deliver the response."""
        if self._client is None or self._callback_server is None:
            logger.error(
                "KoreAIAdapter not started, dropping message for room %s", room_id
            )
            return

        # Initialize or restore room state
        room_state = self._ensure_room_state(room_id, history, is_session_bootstrap)

        # Serialize messages per room
        lock = self._room_locks.setdefault(room_id, asyncio.Lock())
        async with lock:
            await self._process_message(msg, tools, room_state, room_id)

    async def _process_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        room_state: KoreAIRoomState,
        room_id: str,
    ) -> None:
        """Process a single message (called under per-room lock)."""
        assert self._client is not None
        assert self._callback_server is not None

        try:
            callback_data = await self._send_and_collect(msg, room_state, room_id)
        except Exception as exc:
            logger.exception(
                "Error processing Kore.ai turn for room %s: %s", room_id, exc
            )
            await tools.send_event(
                content="Kore.ai error: %s" % exc,
                message_type="error",
                metadata={"koreai_error": str(exc)},
            )
            return

        # Deliver text messages to the chat room
        for text in callback_data.messages:
            await tools.send_message(
                content=text,
                mentions=[msg.sender_id],
            )

        # Handle task completion
        if callback_data.task_completed:
            if callback_data.is_agent_transfer:
                await tools.send_event(
                    content="Kore.ai bot initiated handoff",
                    message_type="task",
                    metadata={
                        "koreai_end_reason": callback_data.end_reason,
                        "koreai_task_name": callback_data.task_name,
                    },
                )
            else:
                logger.info(
                    "Kore.ai task completed for room %s: reason=%s, task=%s",
                    room_id,
                    callback_data.end_reason,
                    callback_data.task_name,
                )

        # Handle timeout (no messages and no task completion)
        if not callback_data.messages and not callback_data.task_completed:
            await tools.send_event(
                content="Kore.ai bot did not respond within %ds"
                % self.config.response_timeout_seconds,
                message_type="error",
                metadata={"koreai_timeout": True},
            )

        # Update activity timestamp
        room_state.last_activity = time.time()

        # Persist session state to platform history
        await tools.send_event(
            content="koreai session active",
            message_type="task",
            metadata={
                "koreai_identity": room_id,
                "koreai_last_activity": room_state.last_activity,
            },
        )

    async def _send_and_collect(
        self,
        msg: PlatformMessage,
        room_state: KoreAIRoomState,
        room_id: str,
    ) -> CallbackData:
        """Send message to Kore.ai and collect callbacks.

        Acquires the global callback lock so only one room has an inflight
        turn at a time (Kore.ai callbacks don't include the room_id).
        """
        assert self._client is not None
        assert self._callback_server is not None

        now = time.time()

        # Check if session has expired
        new_session = room_state.is_new_session
        if not new_session and room_state.last_activity is not None:
            elapsed = now - room_state.last_activity
            if elapsed > self.config.session_timeout_seconds:
                logger.info(
                    "Kore.ai session expired for room %s (%.0fs idle), starting fresh",
                    room_id,
                    elapsed,
                )
                new_session = True

        async with self._callback_server.active_room_lock:
            collector = self._callback_server.register_turn(room_id)

            try:
                await self._client.send_message(
                    room_id=room_id,
                    text=msg.content,
                    new_session=new_session,
                )
                room_state.is_new_session = False

                return await collector.wait_for_messages(
                    timeout=self.config.response_timeout_seconds,
                )
            finally:
                self._callback_server.unregister_turn(room_id)

    async def on_cleanup(self, room_id: str) -> None:
        """Clean up state for a room.

        Sends SESSION_CLOSURE to Kore.ai and removes per-room state.
        Safe to call multiple times (idempotent).
        """
        # Send session closure to Kore.ai (best-effort)
        if self._client is not None and room_id in self._room_states:
            try:
                await self._client.close_session(room_id)
            except Exception:
                logger.warning("Failed to send session closure for room %s", room_id)

        # Cancel any pending callbacks
        if self._callback_server is not None:
            self._callback_server.unregister_turn(room_id)

        # Remove per-room state
        self._room_states.pop(room_id, None)
        self._room_locks.pop(room_id, None)

        # Shut down server and client when the last room is cleaned up
        if not self._room_states:
            await self._shutdown()

        await super().on_cleanup(room_id)

    def _ensure_room_state(
        self,
        room_id: str,
        history: KoreAISessionState,
        is_bootstrap: bool,
    ) -> KoreAIRoomState:
        """Get or create room state, rehydrating from history on bootstrap."""
        if room_id in self._room_states:
            return self._room_states[room_id]

        room_state = KoreAIRoomState(from_id=room_id)

        # Rehydrate from history on bootstrap
        if is_bootstrap and history.koreai_identity:
            room_state.last_activity = history.koreai_last_activity
            room_state.is_new_session = False

            # Check if session would be expired
            if room_state.last_activity is not None:
                elapsed = time.time() - room_state.last_activity
                if elapsed > self.config.session_timeout_seconds:
                    room_state.is_new_session = True
                    logger.info(
                        "Rehydrated room %s with expired session (%.0fs idle)",
                        room_id,
                        elapsed,
                    )
                else:
                    logger.info(
                        "Rehydrated room %s with active session (%.0fs idle)",
                        room_id,
                        elapsed,
                    )

        self._room_states[room_id] = room_state
        return room_state

    async def _shutdown(self) -> None:
        """Shut down the callback server and HTTP client."""
        if self._callback_server:
            await self._callback_server.stop()
            self._callback_server = None

        if self._client:
            await self._client.close()
            self._client = None
