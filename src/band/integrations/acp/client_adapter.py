"""ACP adapter that bridges Band rooms to a remote ACP runtime."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, ClassVar

from acp import spawn_agent_process
from acp.schema import HttpMcpServer, SseMcpServer

from band.converters.acp_client import ACPClientHistoryConverter
from band.core.protocols import AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.types import AdapterFeatures, Capability, Emit, PlatformMessage
from band.integrations.acp.client_profiles import ACPClientProfile
from band.integrations.acp.client_runtime import (
    ACPConnectionProtocol,
    ACPRuntime,
    PermissionHandler,
)
from band.integrations.acp.client_types import (
    ACPClientSessionState,
    BandACPClient,
)
from band.integrations.mcp.backends import (
    BandMCPBackend,
    create_band_mcp_backend,
)
from band.runtime.custom_tools import CustomToolDef
from band.runtime.mcp_server import LocalMCPServer
from band.runtime.tools import iter_tool_definitions

logger = logging.getLogger(__name__)

LocalMcpServerConfig = HttpMcpServer | SseMcpServer


class ACPClientAdapter(SimpleAdapter[ACPClientSessionState]):
    """Adapter that forwards Band messages to a remote ACP agent.

    The adapter owns Band bridge concerns such as room-to-session mapping,
    session rehydration, system-context bootstrapping, Band MCP injection,
    and emitting replies back to the platform. ACP subprocess lifecycle,
    prompt delivery, and session-update buffering live in ``ACPRuntime``.
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset()
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset()

    def __init__(
        self,
        command: str | list[str],
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        mcp_servers: list[dict[str, Any]] | None = None,
        additional_tools: list[CustomToolDef] | None = None,
        rest_url: str | None = None,
        inject_band_tools: bool = True,
        auth_method: str | None = None,
        profile: ACPClientProfile | None = None,
        features: AdapterFeatures | None = None,
    ) -> None:
        super().__init__(
            history_converter=ACPClientHistoryConverter(),
            features=features,
        )
        self._command = command if isinstance(command, list) else [command]
        self._env = env
        self._cwd = os.path.abspath(cwd or ".")
        self._mcp_servers = list(mcp_servers or [])
        self._custom_tools: list[CustomToolDef] = list(additional_tools or [])
        self._rest_url = rest_url or "https://app.band.ai"
        self._validate_rest_url(self._rest_url)
        self._inject_band_tools = inject_band_tools
        self._auth_method = auth_method
        self._profile = profile

        self._runtime = ACPRuntime(
            command=self._command,
            env=self._env,
            auth_method=self._auth_method,
            client_factory=lambda: BandACPClient(profile=self._profile),
            spawn_process=lambda client, *args, **kwargs: spawn_agent_process(
                client,
                *args,
                **kwargs,
            ),
        )

        self._room_to_session: dict[str, str] = {}
        self._room_tools: dict[str, AgentToolsProtocol] = {}
        self._band_mcp_backend: BandMCPBackend | None = None
        self._band_mcp_server: LocalMCPServer | None = None
        self._bootstrapped_sessions: set[str] = set()
        self._session_lock = asyncio.Lock()

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        await super().on_started(agent_name, agent_description)
        await self._spawn_process()

    async def _spawn_process(self) -> None:
        await self._runtime.start(respawn=False)

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: ACPClientSessionState,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        del participants_msg, contacts_msg
        await self._ensure_connection()

        if is_session_bootstrap and history:
            async with self._session_lock:
                self._rehydrate(room_id, history)

        if self._inject_band_tools:
            async with self._session_lock:
                self._room_tools[room_id] = tools

        session_id = await self._get_or_create_session(room_id)
        self._runtime.reset_session(session_id)
        self._runtime.set_permission_handler(
            session_id,
            self._make_permission_handler(tools, room_id),
        )

        prompt_text = msg.content
        async with self._session_lock:
            needs_bootstrap = session_id not in self._bootstrapped_sessions
            if needs_bootstrap:
                self._bootstrapped_sessions.add(session_id)
        if needs_bootstrap:
            system_context = self._build_system_context(room_id, msg)
            prompt_text = f"{system_context}\n\n{msg.content}"

        try:
            chunks = await self._runtime.prompt(
                session_id=session_id,
                prompt_text=prompt_text,
            )
            sender_name = msg.sender_name or msg.sender_id or "Unknown"
            mentions = [{"id": msg.sender_id, "name": sender_name}]
            for chunk in chunks:
                match chunk.chunk_type:
                    case "text":
                        if chunk.content:
                            await tools.send_message(
                                content=chunk.content,
                                mentions=mentions,
                            )
                    case "thought":
                        await tools.send_event(
                            content=chunk.content,
                            message_type="thought",
                            metadata=chunk.metadata,
                        )
                    case "tool_call" | "tool_result":
                        await tools.send_event(
                            content=chunk.content,
                            message_type=chunk.chunk_type,
                            metadata=chunk.metadata,
                        )
                    case "plan":
                        await tools.send_event(
                            content=chunk.content,
                            message_type="task",
                            metadata=chunk.metadata,
                        )
        except Exception as e:
            logger.exception("ACP agent error: %s", e)
            await self.stop()
            await tools.send_event(
                content=f"ACP agent error: {e}",
                message_type="error",
                metadata={"acp_error": str(e)},
            )
            return

        await tools.send_event(
            content="ACP client session",
            message_type="task",
            metadata={
                "acp_client_session_id": session_id,
                "acp_client_room_id": room_id,
            },
        )

    def _make_permission_handler(
        self,
        tools: AgentToolsProtocol,
        room_id: str,
    ) -> PermissionHandler:
        async def handler(
            options: object,
            session_id: str,
            tool_call: object,
            **kwargs: object,
        ) -> dict[str, object]:
            del options, kwargs
            tool_name = getattr(tool_call, "title", None) or getattr(
                tool_call,
                "name",
                "unknown",
            )
            tool_call_id = getattr(tool_call, "tool_call_id", "")

            logger.info(
                "Permission request: tool=%s, session=%s, room=%s",
                tool_name,
                session_id,
                room_id,
            )

            await tools.send_event(
                content=f"Permission requested: {tool_name}",
                message_type="tool_call",
                metadata={
                    "permission_request": True,
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                    "acp_session_id": session_id,
                    "auto_allowed": True,
                },
            )

            return {"outcome": {"outcome": "allowed"}}

        return handler

    @staticmethod
    def _validate_rest_url(rest_url: str) -> None:
        if not rest_url.startswith(("http://", "https://")):
            raise ValueError("rest_url must be a valid HTTP(S) URL")

    def _build_system_context(self, room_id: str, msg: PlatformMessage) -> str:
        from band.runtime.prompts import render_system_prompt

        agent_name = self.agent_name or "Agent"
        agent_desc = self.agent_description or "An AI assistant"
        requester_name = msg.sender_name or msg.sender_id or "Unknown"
        requester_id = msg.sender_id or "unknown"

        system_prompt = render_system_prompt(
            agent_name=agent_name,
            agent_description=agent_desc,
            include_base_instructions=False,
            features=self.features,
        )

        room_context = (
            f"\n## Room Context\n"
            f"You are connected to Band using the Band tools.\n"
            f"Use the Band tools for any visible room action. Plain text "
            f"output is not posted back to the room.\n"
            f"\n"
            f"Current room_id: {room_id}\n"
            f"Current requester name: {requester_name}\n"
            f"Current requester id: {requester_id}\n"
            f"\n"
            f"All Band tool calls must include room_id.\n"
        )

        return f"[System Context]\n{system_prompt}\n{room_context}"

    def _build_local_mcp_server_config(
        self,
        local_server: LocalMCPServer,
    ) -> LocalMcpServerConfig:
        if self._runtime._agent_mcp_transport == "sse":
            return SseMcpServer(
                type="sse",
                name="band",
                url=local_server.sse_url,
                headers=[],
            )

        return HttpMcpServer(
            type="http",
            name="band",
            url=local_server.http_url,
            headers=[],
        )

    async def _get_or_start_band_mcp_server(self) -> LocalMcpServerConfig:
        backend = self._band_mcp_backend
        if backend is None:
            backend = await create_band_mcp_backend(
                kind=self._runtime._agent_mcp_transport,
                tool_definitions=list(iter_tool_definitions(include_memory=False)),
                get_tools=self._room_tools.get,
                additional_tools=self._custom_tools,
            )
            self._band_mcp_backend = backend
            self._band_mcp_server = backend.local_server

        local_server = backend.local_server
        if local_server is None:
            raise RuntimeError("ACP MCP backend did not create a local server")

        return self._build_local_mcp_server_config(local_server)

    async def _get_or_create_session(self, room_id: str) -> str:
        if room_id in self._room_to_session:
            return self._room_to_session[room_id]

        async with self._session_lock:
            if room_id in self._room_to_session:
                return self._room_to_session[room_id]

            mcp_servers: list[object] = list(self._mcp_servers)
            if self._inject_band_tools:
                mcp_servers.append(await self._get_or_start_band_mcp_server())

            session_id = await self._runtime.create_session(
                cwd=self._cwd,
                mcp_servers=mcp_servers,
            )
            self._room_to_session[room_id] = session_id
            logger.info(
                "Created ACP session %s for room %s (mcp_servers=%d)",
                session_id,
                room_id,
                len(mcp_servers),
            )
            return session_id

    async def on_cleanup(self, room_id: str) -> None:
        async with self._session_lock:
            session_id = self._room_to_session.pop(room_id, None)
            self._room_tools.pop(room_id, None)
            if session_id:
                self._bootstrapped_sessions.discard(session_id)

        logger.debug("Cleaned up ACP client resources for room %s", room_id)

    async def stop(self) -> None:
        async with self._session_lock:
            self._room_to_session.clear()
            self._room_tools.clear()
            self._bootstrapped_sessions.clear()
            backend = self._band_mcp_backend
            local_mcp_server = self._band_mcp_server
            self._band_mcp_backend = None
            self._band_mcp_server = None
        if backend is not None:
            await backend.stop()
        elif local_mcp_server is not None:
            await local_mcp_server.stop()
        await self._runtime.stop()
        logger.info("ACP client adapter stopped")

    def _rehydrate(self, room_id: str, history: ACPClientSessionState) -> None:
        del room_id
        for restored_room_id, session_id in history.room_to_session.items():
            if restored_room_id not in self._room_to_session:
                self._room_to_session[restored_room_id] = session_id
                logger.debug(
                    "Restored ACP client session mapping: %s -> %s",
                    restored_room_id,
                    session_id,
                )

        logger.info(
            "Rehydrated ACP client state: %d room-session mappings",
            len(self._room_to_session),
        )

    async def _ensure_connection(self) -> ACPConnectionProtocol:
        return await self._runtime.ensure_connection(
            can_respawn=bool(self.agent_name),
        )
