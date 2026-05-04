"""ACP Server Adapter that bridges ACP protocol to Thenvoi platform."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from thenvoi.client.rest import (
    AsyncRestClient,
    ChatEventRequest,
    ChatMessageRequest,
    ChatMessageRequestMentionsItem,
    ChatRoomRequest,
    DEFAULT_REQUEST_OPTIONS,
)
from thenvoi.converters.acp_server import ACPServerHistoryConverter
from thenvoi.core.protocols import AgentToolsProtocol
from thenvoi.core.simple_adapter import SimpleAdapter
from thenvoi.core.types import PlatformMessage
from thenvoi.integrations.acp.event_converter import EventConverter
from thenvoi.integrations.acp.types import ACPSessionState, PendingACPPrompt

if TYPE_CHECKING:
    from acp.interfaces import Client

    from thenvoi.integrations.acp.push_handler import ACPPushHandler
    from thenvoi.integrations.acp.router import AgentRouter

logger = logging.getLogger(__name__)

# Maximum active ACP sessions per adapter instance.
_MAX_SESSIONS = 100

# Maximum time (seconds) to wait for a Thenvoi peer to respond to a prompt.
# Prevents infinite hangs if the peer is unreachable or unresponsive.
_PROMPT_TIMEOUT_SECONDS = 300

# Allow a short quiet period before completing a prompt so split text replies
# can be forwarded as one logical response.
_PROMPT_COMPLETION_GRACE_SECONDS = 0.25


class ThenvoiACPServerAdapter(SimpleAdapter[ACPSessionState]):
    """Bridge between ACP protocol and Thenvoi platform.

    This adapter enables editors (Zed, Cursor, JetBrains, Neovim) to
    interact with Thenvoi platform peers through the ACP protocol. It acts
    as the "Super-Agent" facade: a single ACP agent that routes to
    multiple Thenvoi peers.

    Uses direct REST client (not AgentToolsProtocol) because:
    - AgentToolsProtocol is room-bound (passed in on_message with room context)
    - ACP server receives requests outside of on_message() context
    - Server needs to send messages to SPECIFIC rooms

    Example:
        from thenvoi import Agent
        from thenvoi.integrations.acp import ThenvoiACPServerAdapter, ACPServer

        adapter = ThenvoiACPServerAdapter(
            rest_url="https://app.thenvoi.com",
            api_key="your-api-key",
        )
        server = ACPServer(adapter)
        agent = Agent.create(adapter=adapter, agent_id="...", api_key="...")
        await agent.start()
        await run_agent(server)
    """

    def __init__(
        self,
        rest_url: str = "https://app.thenvoi.com",
        api_key: str = "",
    ) -> None:
        """Initialize ACP server adapter.

        Args:
            rest_url: Base URL for Thenvoi REST API.
            api_key: API key for authentication.
        """
        super().__init__(history_converter=ACPServerHistoryConverter())

        # Direct REST client for room/message operations
        self._rest = AsyncRestClient(base_url=rest_url, api_key=api_key)

        # Session state (all dicts guarded by _state_lock)
        self._session_to_room: dict[str, str] = {}  # ACP session_id -> room_id
        self._room_to_session: dict[str, str] = {}  # room_id -> session_id
        self._pending_prompts: dict[str, PendingACPPrompt] = {}  # room_id -> pending
        self._session_modes: dict[str, str] = {}  # session_id -> mode_id
        self._session_models: dict[str, str] = {}  # session_id -> model_id
        self._session_cwd: dict[str, str] = {}  # session_id -> cwd
        self._session_mcp_servers: dict[
            str, list[Any]
        ] = {}  # session_id -> mcp_servers
        self._sessions_in_flight = 0
        self._state_lock = asyncio.Lock()

        # ACP client reference for sending session_update
        self._acp_client: Client | None = None

        # Agent identity (set in on_started, used for mention filtering)
        self._agent_id: str | None = None

        # Router for slash commands and mode-based routing
        self._router: AgentRouter | None = None

        # Push handler for unsolicited updates
        self._push_handler: ACPPushHandler | None = None

    # ── Public accessors (used by ACPServer and ACPPushHandler) ──
    #
    # These accessors read/write dicts guarded by _state_lock. Since they
    # are called from sync ACP protocol handlers (which cannot await the
    # lock), they use snapshot copies for reads and best-effort writes.
    # The async methods (create_session, on_cleanup, etc.) use the lock
    # directly.

    def set_acp_client(self, client: Client) -> None:
        """Store ACP client reference for sending session_update.

        Args:
            client: The connected ACP client interface.
        """
        self._acp_client = client

    def get_acp_client(self) -> Client | None:
        """Return the connected ACP client, or None if not connected."""
        return self._acp_client

    def has_session(self, session_id: str) -> bool:
        """Check whether an ACP session is active."""
        return session_id in self._session_to_room

    def get_session_ids(self) -> list[str]:
        """Return a snapshot list of all active ACP session IDs.

        Returns a copy to prevent RuntimeError from concurrent dict
        mutation during iteration.
        """
        return list(self._session_to_room)

    def set_session_mode(self, session_id: str, mode_id: str) -> None:
        """Record the session mode chosen by the editor.

        Note: Called from sync ACP handlers. Safe because the single ACP
        stdio connection serializes requests, so no concurrent callers.
        Cleanup races are prevented by best-effort .pop() in on_cleanup.
        """
        self._session_modes[session_id] = mode_id

    def set_session_model(self, session_id: str, model_id: str) -> None:
        """Record the model chosen by the editor.

        Note: Called from sync ACP handlers. Safe because the single ACP
        stdio connection serializes requests, so no concurrent callers.
        """
        self._session_models[session_id] = model_id
        logger.debug("Session model set: session=%s, model=%s", session_id, model_id)

    def get_session_cwd(self, session_id: str) -> str:
        """Return the working directory for a session, or '.' if unknown."""
        return self._session_cwd.get(session_id, ".")

    def update_session_context(
        self,
        session_id: str,
        *,
        cwd: str | None = None,
        mcp_servers: list[Any] | None = None,
    ) -> None:
        """Update the stored editor context for an existing session."""
        if cwd:
            self._session_cwd[session_id] = cwd
        if mcp_servers is not None:
            normalized = self._normalize_mcp_servers(mcp_servers)
            if normalized:
                self._session_mcp_servers[session_id] = normalized
            else:
                self._session_mcp_servers.pop(session_id, None)

    def get_session_for_room(self, room_id: str) -> str | None:
        """Return the ACP session_id for a room, or None."""
        return self._room_to_session.get(room_id)

    def get_room_for_session(self, session_id: str) -> str | None:
        """Return the room_id for an ACP session, or None."""
        return self._session_to_room.get(session_id)

    async def verify_credentials(self) -> bool:
        """Validate API key by calling the Thenvoi identity endpoint."""
        try:
            await self._rest.agent_api_identity.get_agent_me(
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
            return True
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            logger.warning("Credential verification failed", exc_info=True)
            return False

    async def close(self) -> None:
        """Close the REST client connection pool.

        Note: accesses Fern-generated client internal ``_client`` because
        the generated ``AsyncRestClient`` does not expose a public close
        method. The ``hasattr`` guard prevents breakage if internals change.
        """
        try:
            if hasattr(self._rest, "_client") and self._rest._client:
                await self._rest._client.aclose()
        except Exception:
            logger.exception("Error closing REST client")
        logger.debug("ACP server adapter closed")

    # ── Composition setters ──

    def set_router(self, router: AgentRouter) -> None:
        """Set the agent router for slash commands and mode-based routing.

        Args:
            router: The router instance.
        """
        self._router = router

    def set_push_handler(self, handler: ACPPushHandler) -> None:
        """Set the push handler for unsolicited session updates.

        Args:
            handler: The push handler instance.
        """
        self._push_handler = handler

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Store agent metadata.

        Args:
            agent_name: Name of this agent.
            agent_description: Description of this agent.
        """
        await super().on_started(agent_name, agent_description)

        # Fetch own agent ID for mention filtering
        try:
            identity = await self._rest.agent_api_identity.get_agent_me(
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
            self._agent_id = identity.data.id
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            logger.error(
                "Could not fetch agent identity for mention filtering. "
                "Self-mention filtering will be disabled. "
                "Check that your API key is valid and the REST URL is reachable.",
                exc_info=True,
            )

        logger.info("ACP server adapter started: %s", agent_name)

    async def create_session(
        self,
        cwd: str = ".",
        mcp_servers: list[Any] | None = None,
    ) -> str:
        """Create a Thenvoi room and map it to an ACP session.

        Args:
            cwd: Working directory from the editor.
            mcp_servers: Optional MCP server configs from the editor.

        Returns:
            The ACP session_id.

        Raises:
            Exception: If room creation or event emission fails.
                State is rolled back on failure to prevent orphaned rooms.
        """
        session_id = uuid4().hex
        async with self._state_lock:
            active_sessions = len(self._session_to_room) + self._sessions_in_flight
            if active_sessions >= _MAX_SESSIONS:
                raise RuntimeError(
                    f"Maximum sessions ({_MAX_SESSIONS}) reached for this ACP adapter"
                )
            self._sessions_in_flight += 1

        try:
            response = await self._rest.agent_api_chats.create_agent_chat(
                chat=ChatRoomRequest(),
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
        except Exception:
            async with self._state_lock:
                self._sessions_in_flight -= 1
            raise

        room_id = response.data.id

        async with self._state_lock:
            self._sessions_in_flight -= 1
            self._session_to_room[session_id] = room_id
            self._room_to_session[room_id] = session_id
            self._session_cwd[session_id] = cwd
            normalized_mcp_servers = self._normalize_mcp_servers(mcp_servers or [])
            if normalized_mcp_servers:
                self._session_mcp_servers[session_id] = normalized_mcp_servers

        try:
            await self._emit_session_event(room_id, session_id)
        except Exception:
            async with self._state_lock:
                self._session_to_room.pop(session_id, None)
                self._room_to_session.pop(room_id, None)
                self._session_cwd.pop(session_id, None)
                self._session_mcp_servers.pop(session_id, None)
            logger.exception(
                "Failed to emit session event for session %s (room %s), "
                "rolling back mappings",
                session_id,
                room_id,
            )
            raise

        logger.info(
            "Created ACP session %s -> room %s (cwd=%s)",
            session_id,
            room_id,
            cwd,
        )

        return session_id

    async def handle_prompt(self, session_id: str, text: str) -> None:
        """Send message to Thenvoi room and wait for response.

        The response is streamed back to the editor via on_message() ->
        session_update, not returned directly from this method.

        Args:
            session_id: The ACP session identifier.
            text: The user's prompt text.

        Raises:
            KeyError: If session_id is not mapped to a room.
        """
        async with self._state_lock:
            room_id = self._session_to_room.get(session_id)
            if room_id is None:
                raise KeyError(
                    f"Unknown ACP session: {session_id}. "
                    "Session may have been cleaned up or never created."
                )
            pending = PendingACPPrompt(session_id=session_id)
            self._pending_prompts[room_id] = pending

            # Read routing state while holding lock
            current_mode = self._session_modes.get(session_id)

        # Route via slash commands or session modes (no lock needed — pure)
        cleaned_text = text
        target_peer: str | None = None
        if self._router:
            cleaned_text, target_peer = self._router.resolve(text, current_mode)
        prompt_text = self._prepend_session_context(session_id, cleaned_text)

        # Get participants for mentions
        participants = (
            await self._rest.agent_api_participants.list_agent_chat_participants(
                chat_id=room_id,
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
        )

        # Filter mentions: exclude only self, not all agents
        if target_peer:
            mentions = [
                ChatMessageRequestMentionsItem(id=p.id, name=p.name)
                for p in participants.data
                if p.id != self._agent_id and p.name == target_peer
            ]
        else:
            mentions = [
                ChatMessageRequestMentionsItem(id=p.id, name=p.name)
                for p in participants.data
                if p.id != self._agent_id
            ]
        mention_text = " ".join(f"@{m.name}" for m in mentions)

        try:
            await self._rest.agent_api_messages.create_agent_chat_message(
                chat_id=room_id,
                message=ChatMessageRequest(
                    content=f"{mention_text} {prompt_text}".strip(),
                    mentions=mentions,
                ),
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
        except Exception:
            await self._finish_pending_prompt(room_id, set_done=True)
            raise

        logger.debug("Sent prompt to room %s, awaiting response", room_id)

        # Wait for on_message() to signal completion, with timeout to prevent
        # infinite hangs if the peer never responds.
        try:
            await asyncio.wait_for(
                pending.done_event.wait(),
                timeout=_PROMPT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            await self._finish_pending_prompt(room_id)
            logger.error(
                "Prompt timed out after %ds for session %s (room %s)",
                _PROMPT_TIMEOUT_SECONDS,
                session_id,
                room_id,
            )
            raise

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: ACPSessionState,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        """Receive Thenvoi response, stream to editor via ACP session_update.

        Args:
            msg: Platform message from peer.
            tools: Agent tools (not used - we use REST client).
            history: Converted history as ACPSessionState.
            participants_msg: Participants update message, or None.
            contacts_msg: Contact changes broadcast message, or None.
            is_session_bootstrap: True if this is first message from room.
            room_id: The room identifier.
        """
        # Rehydrate on bootstrap
        if is_session_bootstrap and history:
            async with self._state_lock:
                self._rehydrate(history)

        # Find pending prompt for this room
        async with self._state_lock:
            pending = self._pending_prompts.get(room_id)

        if pending and self._acp_client:
            # Convert message to rich ACP chunk
            chunk = EventConverter.convert(msg)
            if chunk is not None:
                await self._acp_client.session_update(
                    session_id=pending.session_id,
                    update=chunk,
                )

            message_type = getattr(msg, "message_type", "text")
            if message_type in ("text", "error"):
                pending.terminal_message_seen = True
            if pending.terminal_message_seen:
                await self._schedule_prompt_completion(room_id, pending)
        elif self._acp_client and self._push_handler:
            # No pending prompt — push unsolicited update
            try:
                await self._push_handler.handle_push_event(msg, room_id)
            except Exception:
                logger.exception("Push handler failed for room %s", room_id)
        else:
            logger.debug(
                "Dropping message for room %s: no ACP client or pending prompt",
                room_id,
            )

    async def on_cleanup(self, room_id: str) -> None:
        """Remove all state for a room. Idempotent.

        Args:
            room_id: The room identifier.
        """
        async with self._state_lock:
            # Clean session mappings for this room
            session_id = self._room_to_session.pop(room_id, None)
            if session_id:
                self._session_to_room.pop(session_id, None)
                self._session_modes.pop(session_id, None)
                self._session_models.pop(session_id, None)
                self._session_cwd.pop(session_id, None)
                self._session_mcp_servers.pop(session_id, None)

        await self._finish_pending_prompt(room_id, set_done=True)
        logger.debug("Cleaned up ACP server resources for room %s", room_id)

    async def cancel_prompt(self, session_id: str) -> None:
        """Cancel a pending prompt by setting done_event.

        Args:
            session_id: The ACP session identifier.
        """
        async with self._state_lock:
            room_id = self._session_to_room.get(session_id)
        if room_id:
            pending = await self._finish_pending_prompt(room_id, set_done=True)
            if pending:
                logger.info("Cancelled prompt for session %s", session_id)

    def _rehydrate(self, history: ACPSessionState) -> None:
        """Restore session state from history.

        Args:
            history: Session state extracted from platform history.
        """
        for session_id, room_id in history.session_to_room.items():
            if session_id not in self._session_to_room:
                self._session_to_room[session_id] = room_id
                self._room_to_session[room_id] = session_id
                logger.debug(
                    "Restored ACP session mapping: %s -> %s",
                    session_id,
                    room_id,
                )
            if session_id not in self._session_cwd:
                cwd = history.session_cwd.get(session_id)
                if cwd:
                    self._session_cwd[session_id] = cwd
            if session_id not in self._session_mcp_servers:
                mcp_servers = history.session_mcp_servers.get(session_id)
                if mcp_servers:
                    self._session_mcp_servers[session_id] = list(mcp_servers)

        logger.info(
            "Rehydrated ACP server state: %d sessions",
            len(self._session_to_room),
        )

    async def _emit_session_event(self, room_id: str, session_id: str) -> None:
        """Emit a task event to persist session mapping in history.

        This enables session rehydration when the agent rejoins.

        Args:
            room_id: The room ID.
            session_id: The ACP session ID.
        """
        await self._rest.agent_api_events.create_agent_chat_event(
            chat_id=room_id,
            event=ChatEventRequest(
                content="ACP session context",
                message_type="task",
                metadata={
                    "acp_session_id": session_id,
                    "acp_room_id": room_id,
                    "acp_cwd": self._session_cwd.get(session_id, "."),
                    "acp_mcp_servers": self._session_mcp_servers.get(session_id, []),
                },
            ),
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

    def _prepend_session_context(self, session_id: str, text: str) -> str:
        """Add editor session context so downstream peers can use it."""
        cwd = self._session_cwd.get(session_id)
        mcp_servers = self._session_mcp_servers.get(session_id, [])
        if not cwd and not mcp_servers:
            return text

        lines = ["[ACP Session Context]"]
        if cwd:
            lines.append(f"Editor cwd: {cwd}")
        if mcp_servers:
            lines.append("Editor MCP servers:")
            lines.extend(self._format_mcp_server_lines(mcp_servers))
            lines.append("These MCP servers belong to the connected editor session.")
        lines.append("")
        lines.append(text)
        return "\n".join(lines)

    def _format_mcp_server_lines(
        self,
        mcp_servers: list[dict[str, Any]],
    ) -> list[str]:
        """Render editor MCP servers as compact prompt lines."""
        lines: list[str] = []
        for server in mcp_servers:
            server_type = str(server.get("type") or "unknown")
            server_name = (
                server.get("name")
                or server.get("server_name")
                or server.get("command")
                or server.get("url")
                or "unnamed"
            )
            details: list[str] = [server_type]
            if server_type == "stdio" and server.get("command"):
                details.append(f"command={server['command']}")
            if server_type != "stdio" and server.get("url"):
                details.append(f"url={server['url']}")
            lines.append(f"- {server_name} ({', '.join(details)})")
        return lines

    def _normalize_mcp_servers(
        self,
        mcp_servers: list[Any],
    ) -> list[dict[str, Any]]:
        """Convert ACP MCP server configs to JSON-safe dictionaries."""
        normalized: list[dict[str, Any]] = []
        for server in mcp_servers:
            normalized_server = self._json_safe(server)
            if isinstance(normalized_server, dict):
                normalized.append(normalized_server)
        return normalized

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        """Convert values to JSON-safe structures for history persistence."""
        if value is None or isinstance(value, str | int | float | bool):
            return value
        if isinstance(value, list | tuple):
            return [cls._json_safe(item) for item in value]
        if isinstance(value, dict):
            return {str(key): cls._json_safe(item) for key, item in value.items()}
        if hasattr(value, "model_dump"):
            return cls._json_safe(value.model_dump(mode="json"))
        if hasattr(value, "__dict__"):
            return cls._json_safe(vars(value))

        # Fall back to a plain string so event metadata stays serializable.
        return json.loads(json.dumps(str(value)))

    async def _schedule_prompt_completion(
        self, room_id: str, pending: PendingACPPrompt
    ) -> None:
        """Debounce prompt completion so split replies are not truncated."""
        async with self._state_lock:
            if self._pending_prompts.get(room_id) is not pending:
                return
            if pending.completion_task is not None:
                pending.completion_task.cancel()
            pending.completion_task = asyncio.create_task(
                self._complete_prompt_after_grace(room_id, pending)
            )

    async def _complete_prompt_after_grace(
        self, room_id: str, pending: PendingACPPrompt
    ) -> None:
        """Complete a prompt after a short quiet period."""
        try:
            await asyncio.sleep(_PROMPT_COMPLETION_GRACE_SECONDS)
            await self._finish_pending_prompt(room_id, expected=pending, set_done=True)
        except asyncio.CancelledError:
            raise

    async def _finish_pending_prompt(
        self,
        room_id: str,
        *,
        expected: PendingACPPrompt | None = None,
        set_done: bool = False,
    ) -> PendingACPPrompt | None:
        """Remove a pending prompt and cancel any scheduled completion."""
        async with self._state_lock:
            pending = self._pending_prompts.get(room_id)
            if pending is None:
                return None
            if expected is not None and pending is not expected:
                return None
            pending = self._pending_prompts.pop(room_id)

        if pending.completion_task is not None:
            pending.completion_task.cancel()
            pending.completion_task = None
        if set_done:
            pending.done_event.set()
        return pending
