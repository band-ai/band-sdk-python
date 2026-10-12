"""Connection lifecycle and session operations for outbound ACP clients."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, suppress
from typing import Any, cast

from acp import spawn_agent_process
from acp.schema import (
    ClientCapabilities,
)

from band.core.retention import task_failure
from band.integrations.acp.collecting import ACPCollectingClient
from band.integrations.acp.permissions import (
    ALLOW_ALWAYS_KIND,
    ElicitationHandler,
    ElicitationNarrator,
    PermissionHandler,
    PermissionNarrator,
    allow_permission,
    cancel_permission,
    elicitation_requested_schema,
    elicitation_session_id,
    option_id_of_kind,
    permission_option_ids,
    select_allow_option_id,
)
from band.integrations.acp.sessions import (
    ACP_SESSION_LOAD_TIMEOUT_SECONDS,
    ACPSessionOperations,
)
from band.integrations.acp.stderr import (
    STDERR_DRAIN_TIMEOUT_S,
    STDERR_TAIL_LINES,
    ACPStderrDrain,
)
from band.integrations.acp.stream import ChunkSink
from band.integrations.acp.transport import (
    ACP_STDIO_LIMIT_BYTES,
    ACPConnectionProtocol,
    ACPSpawnContextProtocol,
    tcp_spawn_process,
)
from band.integrations.mcp.backends import BandMCPTransport

__all__ = [
    "ACP_SESSION_LOAD_TIMEOUT_SECONDS",
    "ACP_STDIO_LIMIT_BYTES",
    "ALLOW_ALWAYS_KIND",
    "STDERR_DRAIN_TIMEOUT_S",
    "STDERR_TAIL_LINES",
    "ACPCollectingClient",
    "ACPConnectionProtocol",
    "ACPRuntime",
    "ACPSpawnContextProtocol",
    "ChunkSink",
    "ElicitationHandler",
    "ElicitationNarrator",
    "PermissionHandler",
    "PermissionNarrator",
    "allow_permission",
    "cancel_permission",
    "elicitation_requested_schema",
    "elicitation_session_id",
    "option_id_of_kind",
    "permission_option_ids",
    "select_allow_option_id",
    "tcp_spawn_process",
]

logger = logging.getLogger(__name__)

STDOUT_DISCARD_CHUNK_BYTES = 65536


class ACPRuntime(ACPSessionOperations):
    """Generic ACP subprocess runtime shared by outbound ACP bridges."""

    def __init__(
        self,
        *,
        command: list[str],
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        auth_method: str | None = None,
        client_factory: Callable[[], ACPCollectingClient] | None = None,
        spawn_process: Callable[..., object] | None = None,
        client_capabilities: ClientCapabilities | None = None,
        use_unstable_protocol: bool = False,
        pass_builtin_transport_options: bool = True,
    ) -> None:
        super().__init__()
        self._command = list(command)
        self._env = env
        self._cwd = cwd
        self._auth_method = auth_method
        self._client_factory = client_factory or ACPCollectingClient
        self._spawn_process = spawn_process or spawn_agent_process
        self._client_capabilities = client_capabilities
        self._use_unstable_protocol = use_unstable_protocol
        self._pass_builtin_transport_options = pass_builtin_transport_options

        self._conn: ACPConnectionProtocol | None = None
        self._ctx: (
            AbstractAsyncContextManager[tuple[ACPConnectionProtocol, object]] | None
        ) = None
        self._transport: object = None
        self._exit_attempted = False
        self._closure: asyncio.Task[None] | None = None
        self._stop_lock = asyncio.Lock()
        self._stderr = ACPStderrDrain(logger)

    async def start(self, *, respawn: bool = False) -> None:
        """Spawn or respawn the ACP agent subprocess."""
        async with self._stop_lock:
            await self._start(respawn=respawn)

    async def _start(self, *, respawn: bool) -> None:
        if self._ctx is not None:
            raise RuntimeError("ACP runtime is already started or not yet closed")
        self._connection_failed = False
        logger.info(
            "%s ACP agent subprocess",
            "Respawning" if respawn else "Spawning",
        )

        self._ctx = self._spawn_context()
        try:
            self._conn, self._transport = await self._ctx.__aenter__()
            if isinstance(self._transport, asyncio.subprocess.Process):
                self._stderr.start(self._transport)
            await self._initialize_connection(self._conn)
        except BaseException as error:
            if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt)):
                self._stderr.expect_exit(connection_failed=self._connection_failed)
            await self._close_failed_start()
            raise
        # A connect-only transport (e.g. TCP) carries no command; describe it
        # rather than logging a blank suffix.
        logger.info(
            "Connected to ACP agent: %s",
            " ".join(self._command) or "<injected transport>",
        )

    def _spawn_context(
        self,
    ) -> AbstractAsyncContextManager[tuple[ACPConnectionProtocol, object]]:
        self._client = self._client_factory()  # type: ignore[abstract]  # ACP client protocol defines optional hooks as abstract
        spawn_kwargs: dict[str, Any] = {}
        if self._pass_builtin_transport_options:
            spawn_kwargs["transport_kwargs"] = {"limit": ACP_STDIO_LIMIT_BYTES}
            if self._use_unstable_protocol:
                spawn_kwargs["use_unstable_protocol"] = True
        return cast(
            AbstractAsyncContextManager[tuple[ACPConnectionProtocol, object]],
            self._spawn_process(
                self._client,
                # TCP transports carry no command; stdio carries executable and args.
                *self._command,
                env=self._env,
                cwd=self._cwd,
                **spawn_kwargs,
            ),
        )

    async def _initialize_connection(self, conn: ACPConnectionProtocol) -> None:
        init_kwargs: dict[str, Any] = {"protocol_version": 1}
        if self._client_capabilities is not None:
            init_kwargs["client_capabilities"] = self._client_capabilities
        init_response = await cast(Any, conn).initialize(**init_kwargs)
        self._agent_mcp_transport = self._select_mcp_transport(init_response)
        self._agent_supports_session_load = self._select_session_load(init_response)
        self._agent_supports_session_close = self._select_session_close(init_response)
        if self._auth_method:
            await conn.authenticate(method_id=self._auth_method)
            logger.info("Authenticated with method: %s", self._auth_method)

    async def ensure_connection(self, *, can_respawn: bool) -> ACPConnectionProtocol:
        async with self._stop_lock:
            if self._conn is None:
                if self._ctx is None and can_respawn:
                    await self._start(respawn=False)
                else:
                    raise RuntimeError(
                        "ACP client not initialized. Call on_started first."
                    )

            conn = self._conn

        if conn is None:
            raise RuntimeError("ACP client connection dropped before prompt")
        return conn

    @property
    def closed(self) -> bool:
        """Whether no process or connection remains to close."""
        return self._ctx is None

    async def stop(self) -> None:
        """Close the connection and confirm its process exited.

        The closure is one owned task: a cancelled caller leaves it running
        and the next ``stop`` joins it. After a failed closure the handles stay
        held and the next ``stop`` closes that same process again.

        Raises:
            Exception: The closure failed; the runtime still holds its process.
        """
        if self._ctx is None:
            return
        self._stderr.expect_exit(connection_failed=self._connection_failed)
        await self._closed()

    async def _closed(self) -> None:
        closure = self._closure
        if closure is None or (closure.done() and task_failure(closure) is not None):
            closure = self._closure = asyncio.create_task(self._close())
        await asyncio.shield(closure)

    async def _close(self) -> None:
        try:
            await self._exit_transport()
        finally:
            await self._stderr.finish()
        self._forget_connection()

    async def _exit_transport(self) -> None:
        """Exit the spawn context once; after that exit failed, only the
        process is left to confirm.

        Closure is confirmed at stdout EOF, not exit: ``Process.wait`` returns
        at once for a process that already exited, while a descendant that
        inherited stdout can still hold it.
        """
        process = self._transport
        if not self._exit_attempted and self._ctx is not None:
            self._exit_attempted = True
            await self._ctx.__aexit__(None, None, None)
        elif isinstance(process, asyncio.subprocess.Process):
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
            await process.wait()
        if isinstance(process, asyncio.subprocess.Process) and process.stdout:
            await _read_to_eof(process.stdout)

    def _forget_connection(self) -> None:
        self._ctx = None
        self._conn = None
        self._client = None
        self._transport = None
        self._exit_attempted = False
        self._closure = None
        self._agent_supports_session_load = False
        self._agent_supports_session_close = False

    async def _close_failed_start(self) -> None:
        """Close what a failed start opened; a failed close keeps it for ``stop``."""
        try:
            await self._closed()
        except Exception:
            logger.exception("Error cleaning up ACP subprocess after a failed start")

    @staticmethod
    def _select_mcp_transport(init_response: object) -> BandMCPTransport:
        capabilities = getattr(init_response, "agent_capabilities", None)
        mcp_capabilities = getattr(capabilities, "mcp_capabilities", None)

        if getattr(mcp_capabilities, "http", False):
            return BandMCPTransport.HTTP
        if getattr(mcp_capabilities, "sse", False):
            return BandMCPTransport.SSE

        return BandMCPTransport.HTTP

    @staticmethod
    def _select_session_load(init_response: object) -> bool:
        capabilities = getattr(init_response, "agent_capabilities", None)
        return getattr(capabilities, "load_session", False) is True

    @staticmethod
    def _select_session_close(init_response: object) -> bool:
        capabilities = getattr(init_response, "agent_capabilities", None)
        session_capabilities = getattr(capabilities, "session_capabilities", None)
        return getattr(session_capabilities, "close", None) is not None


async def _read_to_eof(stream: asyncio.StreamReader) -> None:
    while await stream.read(STDOUT_DISCARD_CHUNK_BYTES):
        pass
