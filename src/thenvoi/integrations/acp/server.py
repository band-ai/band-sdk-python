"""ACP protocol handler for Thenvoi platform."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import acp.schema as acp_schema
from acp import (
    Agent,
    InitializeResponse,
    NewSessionResponse,
    PromptResponse,
)
from acp.schema import (
    AgentCapabilities,
    AudioContentBlock,
    AuthenticateResponse,
    EmbeddedResourceContentBlock,
    ForkSessionResponse,
    ImageContentBlock,
    Implementation,
    ListSessionsResponse,
    LoadSessionResponse,
    PromptCapabilities,
    ResourceContentBlock,
    ResumeSessionResponse,
    SessionCapabilities,
    SessionForkCapabilities,
    SessionListCapabilities,
    SessionResumeCapabilities,
    SessionInfo,
    SetSessionConfigOptionResponse,
    SetSessionModeResponse,
    SetSessionModelResponse,
    TextContentBlock,
)

from thenvoi import __version__

if TYPE_CHECKING:
    from acp.interfaces import Client

    from thenvoi.integrations.acp.server_adapter import ThenvoiACPServerAdapter

logger = logging.getLogger(__name__)


class ACPServer(Agent):
    """ACP protocol handler that delegates to ThenvoiACPServerAdapter.

    Subclasses the ACP SDK's Agent class to handle ACP JSON-RPC protocol
    methods (initialize, new_session, prompt, cancel) and delegates the
    actual Thenvoi platform interaction to the adapter.

    This follows the same two-layer pattern as the A2A Gateway:
    - ACPServer: Protocol handler (like GatewayServer)
    - ThenvoiACPServerAdapter: Platform bridge (like A2AGatewayAdapter)
    """

    def __init__(self, adapter: ThenvoiACPServerAdapter) -> None:
        """Initialize ACP server.

        Args:
            adapter: The Thenvoi ACP server adapter for platform interaction.
        """
        self._adapter = adapter
        self._conn: Client | None = None

    def on_connect(self, conn: Client) -> None:
        """Store client reference for sending session_update notifications.

        Called by the ACP SDK when a client connects.

        Args:
            conn: The connected ACP client interface.
        """
        self._conn = conn
        self._adapter.set_acp_client(conn)

    def _auth_method(self, **kwargs: Any) -> Any:
        auth_method = getattr(acp_schema, "AuthMethod", None) or getattr(
            acp_schema, "AuthMethodAgent"
        )
        return auth_method(**kwargs)

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: Any = None,
        client_info: Any = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        """Handle ACP initialize request.

        Returns Thenvoi agent capabilities and info.

        Args:
            protocol_version: ACP protocol version from client.
            client_capabilities: Optional client capabilities.
            client_info: Optional client implementation info.
            **kwargs: Additional keyword arguments.

        Returns:
            InitializeResponse with agent info and protocol version.
        """
        logger.info(
            "ACP initialize: protocol_version=%d, client_info=%s",
            protocol_version,
            client_info,
        )
        return InitializeResponse(  # type: ignore[call-arg]  # Pydantic alias: protocolVersion
            protocol_version=protocol_version,
            agent_capabilities=AgentCapabilities(
                load_session=True,
                prompt_capabilities=PromptCapabilities(
                    # Thenvoi supports rich text and tool/thought updates.
                    # It does not currently consume image/audio prompt blocks.
                    image=False,
                    audio=False,
                    embedded_context=True,
                ),
                session_capabilities=SessionCapabilities(
                    list=SessionListCapabilities(),
                    resume=SessionResumeCapabilities(),
                    fork=SessionForkCapabilities(),
                ),
                field_meta={
                    "streaming": True,
                    "tools": True,
                    "modes": ["default", "code"],
                },
            ),
            agent_info=Implementation(
                name="thenvoi-agent",
                title=self._adapter.agent_name or "Thenvoi Agent",
                version=__version__,
            ),
            auth_methods=[
                self._auth_method(
                    id="api_key",
                    name="API Key",
                    description="Authenticate with THENVOI_API_KEY.",
                ),
            ],
        )

    async def new_session(
        self,
        cwd: str,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        """Handle ACP new_session request.

        Creates a Thenvoi room and maps it to the ACP session. The
        ``cwd`` and ``mcp_servers`` are stored per-session in the adapter
        so they can be returned in ``list_sessions`` and used for
        workspace context.

        Args:
            cwd: Working directory from the editor.
            mcp_servers: Optional MCP server configs from the editor.
            **kwargs: Additional keyword arguments.

        Returns:
            NewSessionResponse with the session identifier.
        """
        session_id = await self._adapter.create_session(
            cwd=cwd,
            mcp_servers=mcp_servers,
        )
        logger.info("Created ACP session %s (cwd=%s)", session_id, cwd)
        return NewSessionResponse(session_id=session_id)  # type: ignore[call-arg]  # Pydantic alias: sessionId

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> LoadSessionResponse | None:
        """Handle ACP load_session request.

        Returns a LoadSessionResponse if the session exists in the
        adapter's active mappings, or None if not found.

        Args:
            cwd: Working directory from the editor.
            session_id: The ACP session to load.
            mcp_servers: Optional list of MCP servers from the editor.
            **kwargs: Additional keyword arguments.

        Returns:
            LoadSessionResponse if session exists, None otherwise.
        """
        if not self._adapter.has_session(session_id):
            logger.debug("load_session: session %s not found", session_id)
            return None

        self._adapter.update_session_context(
            session_id,
            cwd=cwd,
            mcp_servers=mcp_servers,
        )
        logger.info("Loaded ACP session %s", session_id)
        return LoadSessionResponse()

    async def list_sessions(
        self,
        cursor: str | None = None,
        cwd: str | None = None,
        **kwargs: Any,
    ) -> ListSessionsResponse:
        """Handle ACP list_sessions request.

        Returns session info for each active session in the adapter.

        Args:
            cursor: Optional pagination cursor (not used).
            cwd: Optional working directory filter (not used).
            **kwargs: Additional keyword arguments.

        Returns:
            ListSessionsResponse with active sessions.
        """
        sessions = [
            SessionInfo(session_id=sid, cwd=self._adapter.get_session_cwd(sid))  # type: ignore[call-arg]  # Pydantic alias: sessionId
            for sid in self._adapter.get_session_ids()
        ]
        logger.debug("list_sessions: returning %d sessions", len(sessions))
        return ListSessionsResponse(sessions=sessions)

    async def set_session_mode(
        self,
        mode_id: str,
        session_id: str,
        **kwargs: Any,
    ) -> SetSessionModeResponse | None:
        """Handle ACP set_session_mode request.

        Stores the mode for the session in the adapter's state.

        Args:
            mode_id: The mode identifier to set.
            session_id: The ACP session identifier.
            **kwargs: Additional keyword arguments.

        Returns:
            SetSessionModeResponse acknowledgement.
        """
        self._adapter.set_session_mode(session_id, mode_id)
        logger.info("Set session mode: session=%s, mode=%s", session_id, mode_id)
        return SetSessionModeResponse()

    async def set_session_model(
        self,
        model_id: str,
        session_id: str,
        **kwargs: Any,
    ) -> SetSessionModelResponse | None:
        """Handle ACP set_session_model request.

        Stores the model for the session in the adapter's state.

        Args:
            model_id: The model identifier to set.
            session_id: The ACP session identifier.
            **kwargs: Additional keyword arguments.

        Returns:
            SetSessionModelResponse acknowledgement.
        """
        self._adapter.set_session_model(session_id, model_id)
        logger.info("Set session model: session=%s, model=%s", session_id, model_id)
        return SetSessionModelResponse()

    async def set_config_option(
        self,
        config_id: str,
        session_id: str,
        value: str | bool,
        **kwargs: Any,
    ) -> SetSessionConfigOptionResponse | None:
        """Handle ACP set_config_option request.

        Thenvoi ACP adapter does not currently expose configurable ACP
        session options, so we acknowledge the request with ``None``.
        """
        logger.info(
            "Ignoring unsupported session config option: session=%s, config=%s, value=%s",
            session_id,
            config_id,
            value,
        )
        return None

    async def authenticate(
        self,
        method_id: str,
        **kwargs: Any,
    ) -> AuthenticateResponse | None:
        """Handle ACP authenticate request.

        Validates API key by calling the Thenvoi identity endpoint.

        Args:
            method_id: The authentication method. Supports "api_key" and the
                Cursor compatibility alias "cursor_login".
            **kwargs: Additional keyword arguments.

        Returns:
            AuthenticateResponse if successful, None if authentication fails.
        """
        if method_id in ("api_key", "cursor_login"):
            if await self._adapter.verify_credentials():
                logger.info("Authentication successful via %s", method_id)
                return AuthenticateResponse()
            logger.warning("Authentication failed via %s", method_id)
            return None
        logger.debug("Unsupported auth method: %s", method_id)
        return None

    async def fork_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> ForkSessionResponse:
        """Handle ACP fork_session request.

        Creates a new Thenvoi-backed ACP session as a fork target.
        """
        if not self._adapter.has_session(session_id):
            raise KeyError(f"Cannot fork unknown ACP session: {session_id}")

        forked_session_id = await self._adapter.create_session(
            cwd=cwd,
            mcp_servers=mcp_servers,
        )
        logger.info(
            "Forked ACP session %s -> %s (cwd=%s)",
            session_id,
            forked_session_id,
            cwd,
        )
        return ForkSessionResponse(session_id=forked_session_id)  # type: ignore[call-arg]  # Pydantic alias: sessionId

    async def resume_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> ResumeSessionResponse:
        """Handle ACP resume_session request.

        The in-memory adapter can only resume active sessions.
        """
        _ = kwargs
        if not self._adapter.has_session(session_id):
            raise KeyError(f"Cannot resume unknown ACP session: {session_id}")

        self._adapter.update_session_context(
            session_id,
            cwd=cwd,
            mcp_servers=mcp_servers,
        )
        logger.info("Resumed ACP session %s", session_id)
        return ResumeSessionResponse()

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Handle ACP extension method.

        Handles Cursor-specific extension methods and Thenvoi extensions.

        Cursor extensions:
        - cursor/ask_question: Present options to user (auto-selects first)
        - cursor/create_plan: Approve a plan (auto-approves)

        Args:
            method: The extension method name.
            params: Method parameters.

        Returns:
            Response dict with result or error.
        """
        logger.debug("Extension method: %s, params=%s", method, params)

        # Cursor: ask_question — present multiple-choice options
        # Auto-select first option since Thenvoi platform doesn't have
        # interactive UI prompts (the agent should just proceed).
        if method == "cursor/ask_question":
            options = params.get("options", [])
            if options:
                selected = options[0]
                option_id = selected.get("optionId") or selected.get("id") or "0"
                logger.info(
                    "cursor/ask_question: auto-selected option %s",
                    option_id,
                )
                return {"outcome": {"type": "selected", "optionId": option_id}}
            return {"outcome": {"type": "cancelled"}}

        # Cursor: create_plan — request plan approval
        # Auto-approve since the Thenvoi platform agent should proceed.
        if method == "cursor/create_plan":
            logger.info("cursor/create_plan: auto-approved")
            return {"outcome": {"type": "approved"}}

        return {"error": f"Unknown extension method: {method}"}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        """Handle ACP extension notification (fire-and-forget).

        Handles Cursor-specific notifications by forwarding relevant
        information to the Thenvoi platform as events.

        Cursor notifications:
        - cursor/update_todos: Todo list state changes
        - cursor/task: Subagent task completion

        Args:
            method: The extension notification name.
            params: Notification parameters.
        """
        logger.debug("Extension notification: %s, params=%s", method, params)

        # Forward Cursor notifications as platform events if we have
        # an active session for the notification's context.
        if method.startswith("cursor/"):
            session_id = params.get("sessionId") or params.get("session_id")
            if session_id and self._adapter.has_session(session_id):
                acp_client = self._adapter.get_acp_client()
                if acp_client:
                    from acp import update_agent_message_text

                    # Forward as informational text update
                    match method:
                        case "cursor/update_todos":
                            todos = params.get("todos", [])
                            if todos:
                                summary = "\n".join(
                                    f"- [{'x' if t.get('completed') else ' '}] "
                                    f"{t.get('content', '')}"
                                    for t in todos
                                )
                                await acp_client.session_update(
                                    session_id=session_id,
                                    update=update_agent_message_text(summary),
                                )
                        case "cursor/task":
                            task_result = params.get("result", "")
                            if task_result:
                                await acp_client.session_update(
                                    session_id=session_id,
                                    update=update_agent_message_text(
                                        f"[Task completed] {task_result}"
                                    ),
                                )

    async def prompt(
        self,
        prompt: list[
            TextContentBlock
            | ImageContentBlock
            | AudioContentBlock
            | ResourceContentBlock
            | EmbeddedResourceContentBlock
        ],
        session_id: str,
        message_id: str | None = None,
        **kwargs: Any,
    ) -> PromptResponse:
        """Handle ACP prompt request.

        Extracts text from content blocks, forwards to Thenvoi platform,
        and waits for the response to be streamed back via session_update.

        Args:
            prompt: List of ACP content blocks (TextContentBlock, etc.).
            session_id: The ACP session identifier.
            message_id: Optional message identifier from the client.
            **kwargs: Additional keyword arguments.

        Returns:
            PromptResponse with stop reason.
        """
        del message_id
        text = self._extract_text(prompt)
        logger.debug("ACP prompt for session %s: %s", session_id, text[:100])
        await self._adapter.handle_prompt(session_id, text)
        return PromptResponse(stop_reason="end_turn")  # type: ignore[call-arg]  # Pydantic alias: stopReason

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        """Handle ACP cancel request.

        Cancels a pending prompt for the given session.

        Args:
            session_id: The ACP session identifier.
            **kwargs: Additional keyword arguments.
        """
        logger.info("ACP cancel for session %s", session_id)
        await self._adapter.cancel_prompt(session_id)

    async def close_session(self, session_id: str, **kwargs: Any) -> None:
        """Handle ACP close_session request.

        Cleans up all state for the session via the adapter.

        Args:
            session_id: The ACP session identifier.
            **kwargs: Additional keyword arguments.

        Returns:
            None.
        """
        room_id = self._adapter.get_room_for_session(session_id)
        if room_id is None:
            logger.debug("close_session: session %s not found", session_id)
            return None
        logger.info("Closing ACP session %s (room %s)", session_id, room_id)
        await self._adapter.on_cleanup(room_id)
        return None

    @staticmethod
    def _extract_text(prompt: list[Any]) -> str:
        """Extract text from ACP content blocks.

        Handles TextContentBlock, ImageContentBlock (via URI/description),
        and ResourceContentBlock (via title/URI). Unknown block types are
        skipped with a debug log.

        Args:
            prompt: List of ACP content blocks.

        Returns:
            Concatenated text representation of all content blocks.
        """
        parts: list[str] = []
        for block in prompt:
            if isinstance(block, dict):
                block_type = block.get("type", "text")
                if block_type == "text":
                    text = block.get("text", "")
                    if text:
                        parts.append(str(text))
                elif block_type == "image":
                    uri = block.get("uri", "")
                    parts.append(f"[Image: {uri}]" if uri else "[Image]")
                elif block_type == "resource":
                    title = block.get("title") or block.get("name") or ""
                    uri = block.get("uri", "")
                    desc = block.get("description", "")
                    label = title or uri or "resource"
                    parts.append(f"[Resource: {label}]" + (f" {desc}" if desc else ""))
                else:
                    logger.debug("Unknown content block type: %s", block_type)
            else:
                block_type = getattr(block, "type", "text")
                if block_type == "text":
                    text = getattr(block, "text", "")
                    if text:
                        parts.append(str(text))
                elif block_type == "image":
                    uri = getattr(block, "uri", "")
                    parts.append(f"[Image: {uri}]" if uri else "[Image]")
                elif block_type == "resource":
                    title = getattr(block, "title", "") or getattr(block, "name", "")
                    uri = getattr(block, "uri", "")
                    desc = getattr(block, "description", "")
                    label = title or uri or "resource"
                    parts.append(f"[Resource: {label}]" + (f" {desc}" if desc else ""))
                else:
                    logger.debug("Unknown content block type: %s", block_type)
        return "\n".join(parts)
