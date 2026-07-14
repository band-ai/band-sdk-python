"""ACP adapter that bridges Band rooms to a remote ACP runtime."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections.abc import Callable
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
    allow_permission,
    cancel_permission,
    select_allow_option_id,
    tcp_spawn_process,
)
from band.integrations.acp.client_types import (
    ACPClientSessionState,
    BandACPClient,
)
from band.integrations.mcp.backends import (
    BandMCPBackend,
    create_band_mcp_backend,
)
from band.integrations.acp.types import CollectedChunk, PermissionOutcome
from band.runtime.custom_tools import CustomToolDef
from band.runtime.mcp_server import LocalMCPServer
from band.runtime.tools import (
    is_room_posting_tool,
    iter_tool_definitions,
)

logger = logging.getLogger(__name__)

LocalMcpServerConfig = HttpMcpServer | SseMcpServer

# The transport seam: a callable matching ACPRuntime's spawn_process contract —
# ``(client, *command, env=..., transport_kwargs=...) -> async CM yielding (conn, _)``.
# stdio and TCP are the built-in transports; injecting one (e.g. docker exec / ssh,
# or a fake in tests) is the supported extension point.
SpawnProcess = Callable[..., object]


def _resolve_launcher(command: list[str]) -> list[str]:
    """Resolve the launcher to its full path so the subprocess spawns on Windows.

    An npm-installed launcher like ``npx`` is ``npx.cmd`` on Windows, and
    ``create_subprocess_exec`` does not apply PATHEXT to a bare name — so it fails
    with ``FileNotFoundError``. ``shutil.which`` finds the ``.cmd`` shim (and the
    plain binary on POSIX). A name that can't be resolved is left as-is, so a
    genuinely missing binary still fails loudly at spawn.
    """
    if not command:
        return command
    resolved = shutil.which(command[0])
    return [resolved, *command[1:]] if resolved else list(command)


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
        command: str | list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        mcp_servers: list[dict[str, Any]] | None = None,
        additional_tools: list[CustomToolDef] | None = None,
        rest_url: str | None = None,
        inject_band_tools: bool = True,
        auth_method: str | None = None,
        profile: ACPClientProfile | None = None,
        features: AdapterFeatures | None = None,
        # Transport + advanced knobs are keyword-only: this preserves the original
        # positional order (command, env, cwd, …) for existing callers, and TCP /
        # custom-transport wiring reads clearly at the call site.
        *,
        host: str | None = None,
        port: int | None = None,
        custom_section: str = "",
        spawn_process: SpawnProcess | None = None,
    ) -> None:
        super().__init__(
            history_converter=ACPClientHistoryConverter(),
            features=features,
        )
        self._host, self._port = self._resolve_transport(command, host, port)
        # stdio spawns a subprocess from ``command``; TCP dials an already-running
        # ACP server at host/port and passes an empty command to the runtime.
        self._command: list[str]
        if self._host is not None:
            self._command = []
        else:
            # _resolve_transport guarantees command is set when host is None.
            assert command is not None
            self._command = [command] if isinstance(command, str) else list(command)
        self._env = env
        self._cwd = os.path.abspath(cwd or ".")
        self._mcp_servers = list(mcp_servers or [])
        self._custom_tools: list[CustomToolDef] = list(additional_tools or [])
        self._rest_url = rest_url or "https://app.band.ai"
        self._validate_rest_url(self._rest_url)
        self._inject_band_tools = inject_band_tools
        self._auth_method = auth_method
        self._profile = profile
        self._custom_section = custom_section

        # Transport: an explicit spawn_process wins (advanced/custom transports and
        # tests); otherwise default to acp's subprocess spawner (stdio) or a
        # connect-only seam closed over host/port (TCP; see tcp_spawn_process).
        if spawn_process is not None:
            transport: SpawnProcess = spawn_process
        elif self._host is not None and self._port is not None:
            transport = tcp_spawn_process(self._host, self._port)
        else:
            transport = spawn_agent_process

        self._runtime = ACPRuntime(
            command=_resolve_launcher(self._command),
            env=self._env,
            auth_method=self._auth_method,
            client_factory=lambda: BandACPClient(profile=self._profile),
            spawn_process=transport,
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

        if self._inject_band_tools:
            async with self._session_lock:
                self._room_tools[room_id] = tools

        if is_session_bootstrap and history:
            await self._load_persisted_session(room_id, history)

        session_id = await self._get_or_create_session(room_id)
        self._runtime.reset_session(session_id)
        self._runtime.set_permission_handler(
            session_id,
            self._make_permission_handler(tools, room_id),
        )

        prompt_text = await self._build_prompt_text(room_id, session_id, msg)

        try:
            chunks = await self._runtime.prompt(
                session_id=session_id,
                prompt_text=prompt_text,
            )
            sender_name = msg.sender_name or msg.sender_id or "Unknown"
            mentions = [{"id": msg.sender_id, "name": sender_name}]
            # Reply delivery is tool-first with a text fallback, like the other
            # bridge adapters (copilot_sdk / codex): if the turn already posted
            # via a Band messaging tool, relaying its plain text too would
            # duplicate the reply (and leak the agent's narration of the call).
            replied_in_room = self._turn_replied_in_room(chunks)
            # Streaming text/thought deltas are coalesced into one chunk per run by
            # ACPCollectingClient, so one chunk here == one logical message/event.
            for chunk in chunks:
                if chunk.metadata.get("self_reporting"):
                    continue
                match chunk.chunk_type:
                    case "text":
                        if chunk.content and not replied_in_room:
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

    @staticmethod
    def _turn_replied_in_room(chunks: list[CollectedChunk]) -> bool:
        """True when the turn posted to the room via a Band messaging tool.

        Unlike copilot_sdk / codex, which execute Band tools in-process and flip
        a flag at execution time, ACP tool calls may run out-of-process (a remote
        band-mcp server the SDK never sees execute). The ACP session-update
        stream is the one record of the turn that covers both, so detection
        matches the collected tool-call chunks by their reported title (ACP has
        no structured tool-name field). A room-posting call counts once it (or
        its result update) reports ``completed`` — a failed post must not
        suppress the text fallback, or the turn goes silent.
        """
        posting_call_ids: set[str] = set()
        for chunk in chunks:
            metadata = chunk.metadata or {}
            call_id = str(metadata.get("tool_call_id", ""))
            if chunk.chunk_type == "tool_call" and is_room_posting_tool(chunk.content):
                if metadata.get("status") == "completed":
                    return True
                # Correlate with a later result only by a real id. An empty id
                # (a missing tool_call_id) would match any other id-less result —
                # e.g. a non-posting tool's — and falsely suppress the text
                # fallback, silencing the turn.
                if call_id:
                    posting_call_ids.add(call_id)
            elif (
                chunk.chunk_type == "tool_result"
                and call_id in posting_call_ids
                and metadata.get("status") == "completed"
            ):
                return True
        return False

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
            del kwargs
            tool_name = getattr(tool_call, "title", None) or getattr(
                tool_call,
                "name",
                "unknown",
            )
            tool_call_id = getattr(tool_call, "tool_call_id", "")

            # Auto-approve by selecting one of the agent's offered allow options;
            # an ACP grant must reference an offered optionId (not a bare
            # "allowed"), or the agent can't parse the response and aborts.
            option_id = select_allow_option_id(options)

            logger.info(
                "Permission request: tool=%s, session=%s, room=%s, option=%s",
                tool_name,
                session_id,
                room_id,
                option_id,
            )

            permission_metadata = {
                "permission_request": True,
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "acp_session_id": session_id,
                "auto_allowed": option_id is not None,
            }
            await tools.send_event(
                content=f"Permission requested: {tool_name}",
                message_type="tool_call",
                metadata=permission_metadata,
            )

            if option_id is None:
                permission_outcome = PermissionOutcome.CANCELLED
                response = cancel_permission()
            else:
                permission_outcome = PermissionOutcome.APPROVED
                response = allow_permission(option_id)

            await tools.send_event(
                content=f"Permission {permission_outcome.value}",
                message_type="tool_result",
                metadata={
                    **permission_metadata,
                    "permission_outcome": permission_outcome.value,
                },
            )
            return response

        return handler

    @staticmethod
    def _resolve_transport(
        command: str | list[str] | None,
        host: str | None,
        port: int | None,
    ) -> tuple[str | None, int | None]:
        """Validate exactly one transport is configured; return (host, port) for TCP.

        stdio spawns a subprocess from ``command``; TCP connects to an
        already-running ACP server at ``host``/``port``. The two are mutually
        exclusive and one is required.
        """
        # An empty command ("" or []) is not a usable stdio transport — treat it as
        # absent so it fails the "one is required" check below with a clear error,
        # rather than slipping through to crash at spawn time.
        has_command = bool(command)
        has_tcp = host is not None or port is not None
        if has_command and has_tcp:
            raise ValueError(
                "Provide either command (stdio) or host+port (TCP), not both"
            )
        if not has_command and not has_tcp:
            raise ValueError("Provide either command (stdio) or host+port (TCP)")
        if has_tcp and (host is None or port is None):
            raise ValueError("TCP transport requires both host and port")
        return (host, port) if has_tcp else (None, None)

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
            custom_section=self._custom_section,
            include_base_instructions=False,
            features=self.features,
        )

        room_context = (
            f"\n## Room Context\n"
            f"You are connected to Band using the Band tools.\n"
            f"Use the Band tools for any visible room action. If you post a "
            f"message with a Band tool, your plain text output is not also "
            f"posted; otherwise your plain text reply is delivered to the "
            f"room on your behalf. Never both — reply exactly once, and do "
            f"not narrate the tool calls you are about to make.\n"
            f"\n"
            f"Current room_id: {room_id}\n"
            f"Current requester name: {requester_name}\n"
            f"Current requester id: {requester_id}\n"
            f"\n"
            f"Use each MCP tool's schema for its argument names. When a tool needs "
            f"the current room, use the Current room_id value above.\n"
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

            mcp_servers = await self._session_mcp_servers()

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

    async def _session_mcp_servers(self) -> list[object]:
        """The MCP configuration supplied when creating or loading a session."""
        mcp_servers: list[object] = list(self._mcp_servers)
        if self._inject_band_tools:
            mcp_servers.append(await self._get_or_start_band_mcp_server())
        return mcp_servers

    async def _build_prompt_text(
        self,
        room_id: str,
        session_id: str,
        msg: PlatformMessage,
    ) -> str:
        """Add room context on the first prompt sent to an ACP session."""
        async with self._session_lock:
            needs_bootstrap = session_id not in self._bootstrapped_sessions
            if needs_bootstrap:
                self._bootstrapped_sessions.add(session_id)

        if not needs_bootstrap:
            return msg.content

        system_context = self._build_system_context(room_id, msg)
        return f"{system_context}\n\n{msg.content}"

    async def on_cleanup(self, room_id: str) -> None:
        async with self._session_lock:
            session_id = self._room_to_session.pop(room_id, None)
            self._room_tools.pop(room_id, None)
            if session_id:
                self._bootstrapped_sessions.discard(session_id)

        logger.debug("Cleaned up ACP client resources for room %s", room_id)

    async def cleanup_all(self) -> None:
        """Adapter-wide teardown — the hook ``Agent.stop()`` invokes on shutdown.

        The ACP subprocess / TCP connection and the local Band MCP server are started
        adapter-wide in ``on_started`` (not per room), so releasing them belongs here,
        not in per-room ``on_cleanup``. Idempotent — safe to call again from ``stop()``.
        """
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

    async def stop(self) -> None:
        """Tear down now (used by the ``on_message`` error path); see ``cleanup_all``."""
        await self.cleanup_all()

    async def _load_persisted_session(
        self,
        room_id: str,
        history: ACPClientSessionState,
    ) -> None:
        """Accept this room's persisted session ID only after ACP loads it."""
        async with self._session_lock:
            if room_id in self._room_to_session:
                return
            session_id = history.room_to_session.get(room_id)

        if session_id is None:
            return

        loaded = await self._runtime.load_session(
            cwd=self._cwd,
            session_id=session_id,
            mcp_servers=await self._session_mcp_servers(),
        )
        if not loaded:
            logger.info(
                "Persisted ACP session %s is unavailable for room %s; using a new session",
                session_id,
                room_id,
            )
            return

        async with self._session_lock:
            if room_id not in self._room_to_session:
                self._room_to_session[room_id] = session_id
                logger.debug(
                    "Loaded ACP session mapping: %s -> %s",
                    room_id,
                    session_id,
                )

    async def _ensure_connection(self) -> ACPConnectionProtocol:
        return await self._runtime.ensure_connection(
            can_respawn=bool(self.agent_name),
        )
