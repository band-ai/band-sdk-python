"""Generic ACP subprocess runtime for outbound ACP bridges."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Literal, Protocol, cast

from acp import connect_to_agent, spawn_agent_process, text_block
from acp.exceptions import RequestError
from acp.interfaces import Client

from band.integrations.acp.client_profiles import (
    ACPClientProfile,
    NoopACPClientProfile,
)
from band.integrations.acp.types import CollectedChunk
from band.runtime.tools import is_self_reporting_tool

logger = logging.getLogger(__name__)

ACP_STDIO_LIMIT_BYTES = 16 * 1024 * 1024
ACP_SESSION_LOAD_TIMEOUT_SECONDS = 5.0
PermissionHandler = Callable[..., Awaitable[dict[str, object]]]
MCPTransportKind = Literal["http", "sse"]

# ACP grants a tool-call permission by *selecting one of the options the agent
# offered* (each carries an ``optionId`` and a ``kind``); the on-wire response is
# ``{"outcome": {"outcome": "selected", "optionId": ...}}`` or
# ``{"outcome": {"outcome": "cancelled"}}`` (see ``acp.schema`` AllowedOutcome /
# DeniedOutcome). There is no ``"allowed"`` literal — emitting one makes a
# spec-strict agent (e.g. codex-acp) fail to parse the response and abort the turn.
_ALLOW_OPTION_KINDS = ("allow_once", "allow_always")


def select_allow_option_id(options: object) -> str | None:
    """The ``optionId`` of an allow option offered in a permission request, else None.

    Prefers the least-privilege ``allow_once`` over ``allow_always``. Returns None
    when the agent offered no allow option, so the caller cancels rather than
    guessing (selecting a reject option would silently deny). Accepts the ACP
    ``PermissionOption`` objects or plain dicts.
    """
    if not isinstance(options, (list, tuple)):
        return None
    candidates: list[tuple[object, str]] = []
    for option in options:
        if isinstance(option, dict):
            kind = option.get("kind")
            # Coalesce the camelCase (wire/JSON) and snake_case spellings on
            # *absence*, not falsiness — an explicit (if empty) id must not fall
            # through to the alias and get dropped.
            option_id = option.get("optionId")
            if option_id is None:
                option_id = option.get("option_id")
        else:
            kind = getattr(option, "kind", None)
            option_id = getattr(option, "option_id", None)
            if option_id is None:
                option_id = getattr(option, "optionId", None)
        if option_id is not None:
            candidates.append((kind, str(option_id)))
    for preferred in _ALLOW_OPTION_KINDS:
        for kind, option_id in candidates:
            if kind == preferred:
                return option_id
    return None


def allow_permission(option_id: str) -> dict[str, object]:
    """An ACP ``RequestPermissionResponse`` selecting (granting) ``option_id``."""
    return {"outcome": {"outcome": "selected", "optionId": option_id}}


def cancel_permission() -> dict[str, object]:
    """An ACP ``RequestPermissionResponse`` cancelling the request."""
    return {"outcome": {"outcome": "cancelled"}}


def tcp_spawn_process(
    host: str,
    port: int,
    *,
    limit: int = ACP_STDIO_LIMIT_BYTES,
) -> Callable[..., AbstractAsyncContextManager[tuple[object, object]]]:
    """Build a ``spawn_process`` callable that connects to an ACP server over TCP.

    Drop-in for the stdio ``spawn_agent_process`` seam in :class:`ACPRuntime`: the
    runtime dials *into* an already-running ACP server (e.g. ``copilot --acp --port
    N`` in a container) instead of spawning a subprocess. The returned callable
    accepts and ignores the subprocess-shaped args the runtime forwards (the
    command executable/args and ``transport_kwargs``) — host/port are captured
    here — so no core change to ``ACPRuntime.start`` is needed.
    """

    @asynccontextmanager
    async def _connect(
        client: Client,
        *_command: object,
        env: dict[str, str] | None = None,
        transport_kwargs: dict[str, object] | None = None,
    ) -> AsyncIterator[tuple[object, object]]:
        del _command, env, transport_kwargs  # subprocess-only; unused for TCP
        reader, writer = await asyncio.open_connection(host, port, limit=limit)
        # connect_to_agent argument order is (client, input_stream=writer,
        # output_stream=reader) and it type-guards writer: StreamWriter /
        # reader: StreamReader. Unlike spawn_agent_process it does no cleanup,
        # so we close the connection and transport ourselves.
        conn = connect_to_agent(client, writer, reader)
        try:
            yield conn, writer
        finally:
            try:
                await conn.close()
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    logger.debug("Error awaiting TCP writer close", exc_info=True)

    return _connect


class ACPConnectionProtocol(Protocol):
    """Protocol for the ACP agent connection returned by spawn_agent_process."""

    async def initialize(self, *, protocol_version: int) -> object: ...

    async def authenticate(self, *, method_id: str) -> object: ...

    async def new_session(self, *, cwd: str, mcp_servers: list[object]) -> object: ...

    async def load_session(
        self,
        *,
        cwd: str,
        session_id: str,
        mcp_servers: list[object],
    ) -> object: ...

    async def prompt(self, *, session_id: str, prompt: list[object]) -> object: ...


class ACPSpawnContextProtocol(Protocol):
    """Protocol for the spawn_agent_process async context manager."""

    async def __aenter__(self) -> tuple[ACPConnectionProtocol, object]: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> object: ...


class ACPNewSessionProtocol(Protocol):
    """Protocol for ACP session creation responses."""

    session_id: str


class ACPCollectingClient(Client):  # type: ignore[misc]  # ACP Client has optional methods treated as abstract by pyrefly
    """Generic ACP client that buffers session updates by session_id."""

    def __init__(self, profile: ACPClientProfile | None = None) -> None:
        self._profile = profile or NoopACPClientProfile()
        self._session_chunks: dict[str, list[CollectedChunk]] = {}
        self._permission_handlers: dict[str, PermissionHandler] = {}
        self._self_reporting_call_ids: dict[str, set[str]] = {}

    async def session_update(
        self, session_id: str, update: object, **kwargs: object
    ) -> None:
        del kwargs
        chunk = self._chunk_from_update(session_id, update)
        if chunk is not None:
            self._append_chunk(session_id, chunk)

    def _chunk_from_update(
        self, session_id: str, update: object
    ) -> CollectedChunk | None:
        """Parse one ACP session update without mutating the chunk buffer."""
        match getattr(update, "session_update", None):
            case "agent_message_chunk":
                return self._text_chunk(update, "text")
            case "agent_thought_chunk":
                return self._text_chunk(update, "thought")
            case "tool_call":
                return self._tool_call_chunk(session_id, update)
            case "tool_call_update":
                return self._tool_result_chunk(session_id, update)
            case "plan":
                entries = getattr(update, "entries", [])
                plan_text = "\n".join(
                    getattr(entry, "content", str(entry)) for entry in entries
                )
                return CollectedChunk(chunk_type="plan", content=plan_text)
            case _:
                text = self._extract_text_from_content(update)
                return CollectedChunk(chunk_type="text", content=text) if text else None

    def _text_chunk(self, update: object, chunk_type: str) -> CollectedChunk:
        return CollectedChunk(
            chunk_type=chunk_type,
            content=self._extract_text_from_content(update),
        )

    def _tool_call_chunk(self, session_id: str, update: object) -> CollectedChunk:
        tool_call_id = getattr(update, "tool_call_id", "")
        title = getattr(update, "title", "")
        self_reporting = is_self_reporting_tool(title)
        metadata = {
            "tool_call_id": tool_call_id,
            "raw_input": getattr(update, "raw_input", None),
            "status": getattr(update, "status", "in_progress"),
        }
        if self_reporting:
            metadata["self_reporting"] = True
            if tool_call_id:
                self._self_reporting_call_ids.setdefault(session_id, set()).add(
                    tool_call_id
                )
        return CollectedChunk(
            chunk_type="tool_call",
            content=title,
            metadata=metadata,
        )

    def _tool_result_chunk(self, session_id: str, update: object) -> CollectedChunk:
        tool_call_id = getattr(update, "tool_call_id", "")
        status = getattr(update, "status", "completed")
        self_reporting = tool_call_id in self._self_reporting_call_ids.get(
            session_id, set()
        )
        metadata = {
            "tool_call_id": tool_call_id,
            "status": status,
        }
        if self_reporting:
            metadata["self_reporting"] = True
            if status in ("completed", "failed"):
                self._self_reporting_call_ids[session_id].discard(tool_call_id)
        raw_output = getattr(update, "raw_output", "")
        return CollectedChunk(
            chunk_type="tool_result",
            content=str(raw_output) if raw_output else "",
            metadata=metadata,
        )

    # Chunk kinds that arrive as a stream of deltas for one logical message, so a
    # run of them is coalesced into a single chunk (agents emit one delta per token
    # or phrase). tool_call/tool_result/plan are discrete and never merged.
    _COALESCED_CHUNK_TYPES = ("text", "thought")

    def _append_chunk(self, session_id: str, chunk: CollectedChunk) -> None:
        buffer = self._session_chunks.setdefault(session_id, [])
        if (
            buffer
            and chunk.chunk_type in self._COALESCED_CHUNK_TYPES
            and buffer[-1].chunk_type == chunk.chunk_type
        ):
            buffer[-1].content += chunk.content  # merge the streamed delta
        else:
            buffer.append(chunk)

    async def request_permission(  # type: ignore[override]  # ACP Client uses specific types; we widen to object
        self,
        options: object,
        session_id: str,
        tool_call: object,
        **kwargs: object,
    ) -> dict[str, object]:
        handler = self._permission_handlers.get(session_id)
        if handler:
            return await handler(
                options=options,
                session_id=session_id,
                tool_call=tool_call,
                **kwargs,
            )

        logger.debug("Auto-cancelling permission request for session %s", session_id)
        return cancel_permission()

    def set_permission_handler(
        self,
        session_id: str,
        handler: PermissionHandler | None,
    ) -> None:
        if handler is None:
            self._permission_handlers.pop(session_id, None)
        else:
            self._permission_handlers[session_id] = handler

    def reset_session(self, session_id: str) -> None:
        self._session_chunks.pop(session_id, None)
        self._permission_handlers.pop(session_id, None)
        self._self_reporting_call_ids.pop(session_id, None)

    def get_collected_text(self, session_id: str | None = None) -> str:
        if session_id is not None:
            chunks = self._session_chunks.get(session_id, [])
        else:
            chunks = [
                chunk
                for session_chunks in self._session_chunks.values()
                for chunk in session_chunks
            ]
        return "".join(chunk.content for chunk in chunks if chunk.chunk_type == "text")

    def get_collected_chunks(
        self, session_id: str | None = None
    ) -> list[CollectedChunk]:
        if session_id is not None:
            return list(self._session_chunks.get(session_id, []))
        return [
            chunk
            for session_chunks in self._session_chunks.values()
            for chunk in session_chunks
        ]

    async def ext_method(
        self,
        method: str,
        params: dict[str, object],
    ) -> dict[str, object]:
        return await self._profile.ext_method(method, params)

    async def ext_notification(self, method: str, params: dict[str, object]) -> None:
        session_id = str(params.get("sessionId") or params.get("session_id") or "")
        if not session_id:
            return

        chunks = await self._profile.ext_notification(method, params)
        if chunks:
            self._session_chunks.setdefault(session_id, []).extend(chunks)

    @staticmethod
    def _extract_text_from_content(update: object) -> str:
        content = getattr(update, "content", None)
        if content is None:
            return ""
        text = getattr(content, "text", None)
        if text is None and isinstance(content, dict):
            text = content.get("text", "")
        return str(text) if text else ""


class ACPRuntime:
    """Generic ACP subprocess runtime shared by outbound ACP bridges."""

    def __init__(
        self,
        *,
        command: list[str],
        env: dict[str, str] | None = None,
        auth_method: str | None = None,
        client_factory: Callable[[], ACPCollectingClient] | None = None,
        spawn_process: Callable[..., object] | None = None,
    ) -> None:
        self._command = list(command)
        self._env = env
        self._auth_method = auth_method
        self._client_factory = client_factory or ACPCollectingClient
        self._spawn_process = spawn_process or spawn_agent_process

        self._conn: ACPConnectionProtocol | None = None
        self._client: ACPCollectingClient | None = None
        self._ctx: (
            AbstractAsyncContextManager[tuple[ACPConnectionProtocol, object]] | None
        ) = None
        self._stop_lock = asyncio.Lock()
        self._agent_mcp_transport: MCPTransportKind = "http"
        self._agent_supports_session_load = False

    async def start(self, *, respawn: bool = False) -> None:
        """Spawn or respawn the ACP agent subprocess."""
        logger.info(
            "%s ACP agent subprocess",
            "Respawning" if respawn else "Spawning",
        )

        self._client = self._client_factory()  # type: ignore[abstract]  # ACP client protocol defines optional hooks as abstract
        ctx = cast(
            AbstractAsyncContextManager[tuple[ACPConnectionProtocol, object]],
            self._spawn_process(
                self._client,
                # Splat the whole command: stdio forwards executable + args, while
                # a TCP transport passes an empty command (host/port live in the
                # injected spawn_process closure) and receives no positional args.
                *self._command,
                env=self._env,
                transport_kwargs={"limit": ACP_STDIO_LIMIT_BYTES},
            ),
        )
        self._ctx = ctx
        try:
            self._conn, _ = await ctx.__aenter__()
            init_response = await self._conn.initialize(protocol_version=1)
            self._agent_mcp_transport = self._select_mcp_transport(init_response)
            self._agent_supports_session_load = self._select_session_load(init_response)
            if self._auth_method:
                await self._conn.authenticate(method_id=self._auth_method)
                logger.info("Authenticated with method: %s", self._auth_method)
        except (asyncio.CancelledError, KeyboardInterrupt):
            await self._cleanup_failed_start(ctx, "init cancel")
            raise
        except Exception:
            await self._cleanup_failed_start(ctx, "init failure")
            raise
        # A connect-only transport (e.g. TCP) carries no command; describe it
        # rather than logging a blank suffix.
        logger.info(
            "Connected to ACP agent: %s",
            " ".join(self._command) or "<injected transport>",
        )

    async def ensure_connection(self, *, can_respawn: bool) -> ACPConnectionProtocol:
        async with self._stop_lock:
            if self._conn is None:
                if self._ctx is None and can_respawn:
                    await self.start(respawn=True)
                else:
                    raise RuntimeError(
                        "ACP client not initialized. Call on_started first."
                    )

            conn = self._conn

        if conn is None:
            raise RuntimeError("ACP client connection dropped before prompt")
        return conn

    async def create_session(self, *, cwd: str, mcp_servers: list[object]) -> str:
        conn = await self.ensure_connection(can_respawn=False)
        session = cast(
            ACPNewSessionProtocol,
            await conn.new_session(cwd=cwd, mcp_servers=mcp_servers),
        )
        return session.session_id

    async def load_session(
        self,
        *,
        cwd: str,
        session_id: str,
        mcp_servers: list[object],
    ) -> bool:
        """Load a persisted ACP session when the connected agent supports it.

        ACP session IDs are meaningful only to the agent process that owns them.
        A successful ``session/load`` is therefore the boundary where a persisted ID
        becomes usable on this connection. An unsupported, unavailable, or slow load
        returns ``False`` so callers can create a fresh session without blocking a turn.
        """
        if not self._agent_supports_session_load:
            return False

        conn = await self.ensure_connection(can_respawn=False)
        try:
            response = await asyncio.wait_for(
                conn.load_session(
                    cwd=cwd,
                    session_id=session_id,
                    mcp_servers=mcp_servers,
                ),
                timeout=ACP_SESSION_LOAD_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning(
                "ACP session %s did not load within %s seconds",
                session_id,
                ACP_SESSION_LOAD_TIMEOUT_SECONDS,
            )
            return False
        except RequestError as error:
            if not self._is_missing_session_error(error):
                raise
            logger.info("ACP session %s is no longer available", session_id)
            return False
        return response is not None

    async def prompt(
        self, *, session_id: str, prompt_text: str
    ) -> list[CollectedChunk]:
        conn = await self.ensure_connection(can_respawn=False)
        await conn.prompt(session_id=session_id, prompt=[text_block(prompt_text)])
        return self.get_collected_chunks(session_id)

    def reset_session(self, session_id: str) -> None:
        if self._client is not None:
            self._client.reset_session(session_id)

    def set_permission_handler(
        self,
        session_id: str,
        handler: PermissionHandler | None,
    ) -> None:
        if self._client is not None:
            self._client.set_permission_handler(session_id, handler)

    def get_collected_chunks(self, session_id: str) -> list[CollectedChunk]:
        if self._client is None:
            return []
        return self._client.get_collected_chunks(session_id)

    async def stop(self) -> None:
        ctx: AbstractAsyncContextManager[tuple[ACPConnectionProtocol, object]] | None
        async with self._stop_lock:
            ctx = self._ctx
            self._ctx = None
            self._conn = None
            self._client = None
            self._agent_supports_session_load = False
        if ctx is None:
            return
        try:
            await ctx.__aexit__(None, None, None)
        except Exception:
            logger.exception("Error during ACP runtime shutdown")

    async def _cleanup_failed_start(
        self,
        ctx: AbstractAsyncContextManager[tuple[ACPConnectionProtocol, object]],
        reason: str,
    ) -> None:
        try:
            await ctx.__aexit__(None, None, None)
        except Exception:
            logger.exception("Error cleaning up ACP subprocess after %s", reason)
        self._ctx = None
        self._conn = None
        self._agent_supports_session_load = False

    @staticmethod
    def _select_mcp_transport(init_response: object) -> MCPTransportKind:
        capabilities = getattr(init_response, "agent_capabilities", None)
        mcp_capabilities = getattr(capabilities, "mcp_capabilities", None)

        if getattr(mcp_capabilities, "http", False):
            return "http"
        if getattr(mcp_capabilities, "sse", False):
            return "sse"

        return "http"

    @staticmethod
    def _select_session_load(init_response: object) -> bool:
        capabilities = getattr(init_response, "agent_capabilities", None)
        return getattr(capabilities, "load_session", False) is True

    @staticmethod
    def _is_missing_session_error(error: RequestError) -> bool:
        """Whether an ACP ``session/load`` failure means the session is absent."""
        return error.code == -32002 or (
            "session" in str(error).lower() and "not found" in str(error).lower()
        )
