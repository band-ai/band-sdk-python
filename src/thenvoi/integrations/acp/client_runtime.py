"""Generic ACP subprocess runtime for outbound ACP bridges."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Literal, Protocol, cast

from acp import spawn_agent_process, text_block
from acp.interfaces import Client

from thenvoi.integrations.acp.client_profiles import (
    ACPClientProfile,
    NoopACPClientProfile,
)
from thenvoi.integrations.acp.types import CollectedChunk

logger = logging.getLogger(__name__)

ACP_STDIO_LIMIT_BYTES = 16 * 1024 * 1024
PermissionHandler = Callable[..., Awaitable[dict[str, object]]]
MCPTransportKind = Literal["http", "sse"]


class ACPConnectionProtocol(Protocol):
    """Protocol for the ACP agent connection returned by spawn_agent_process."""

    async def initialize(self, *, protocol_version: int) -> object: ...

    async def authenticate(self, *, method_id: str) -> object: ...

    async def new_session(self, *, cwd: str, mcp_servers: list[object]) -> object: ...

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

    async def session_update(
        self, session_id: str, update: object, **kwargs: object
    ) -> None:
        del kwargs
        discriminator = getattr(update, "session_update", None)
        chunk: CollectedChunk | None = None

        match discriminator:
            case "agent_message_chunk":
                text = self._extract_text_from_content(update)
                chunk = CollectedChunk(chunk_type="text", content=text)
            case "agent_thought_chunk":
                text = self._extract_text_from_content(update)
                chunk = CollectedChunk(chunk_type="thought", content=text)
            case "tool_call":
                tool_call_id = getattr(update, "tool_call_id", "")
                title = getattr(update, "title", "")
                raw_input = getattr(update, "raw_input", None)
                chunk = CollectedChunk(
                    chunk_type="tool_call",
                    content=title,
                    metadata={
                        "tool_call_id": tool_call_id,
                        "raw_input": raw_input,
                        "status": getattr(update, "status", "in_progress"),
                    },
                )
            case "tool_call_update":
                tool_call_id = getattr(update, "tool_call_id", "")
                raw_output = getattr(update, "raw_output", "")
                chunk = CollectedChunk(
                    chunk_type="tool_result",
                    content=str(raw_output) if raw_output else "",
                    metadata={
                        "tool_call_id": tool_call_id,
                        "status": getattr(update, "status", "completed"),
                    },
                )
            case "plan":
                entries = getattr(update, "entries", [])
                plan_text = "\n".join(
                    getattr(entry, "content", str(entry)) for entry in entries
                )
                chunk = CollectedChunk(chunk_type="plan", content=plan_text)
            case _:
                text = self._extract_text_from_content(update)
                if text:
                    chunk = CollectedChunk(chunk_type="text", content=text)

        if chunk is not None:
            self._session_chunks.setdefault(session_id, []).append(chunk)

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
        return {"outcome": {"outcome": "cancelled"}}

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
                self._command[0],
                *self._command[1:],
                env=self._env,
                transport_kwargs={"limit": ACP_STDIO_LIMIT_BYTES},
            ),
        )
        self._ctx = ctx
        try:
            self._conn, _ = await ctx.__aenter__()
            init_response = await self._conn.initialize(protocol_version=1)
            self._agent_mcp_transport = self._select_mcp_transport(init_response)
            if self._auth_method:
                await self._conn.authenticate(method_id=self._auth_method)
                logger.info("Authenticated with method: %s", self._auth_method)
        except (asyncio.CancelledError, KeyboardInterrupt):
            await self._cleanup_failed_start(ctx, "init cancel")
            raise
        except Exception:
            await self._cleanup_failed_start(ctx, "init failure")
            raise
        logger.info("Connected to ACP agent: %s", " ".join(self._command))

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

    @staticmethod
    def _select_mcp_transport(init_response: object) -> MCPTransportKind:
        capabilities = getattr(init_response, "agent_capabilities", None)
        mcp_capabilities = getattr(capabilities, "mcp_capabilities", None)

        if getattr(mcp_capabilities, "http", False):
            return "http"
        if getattr(mcp_capabilities, "sse", False):
            return "sse"

        return "http"
