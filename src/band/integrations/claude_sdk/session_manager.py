"""
Session manager for Claude Agent SDK clients.

Maintains one Claude CLI session per Band chat room, each composed with that
room's ``RetainedRoom``: connect, close and recovery are owned per room, so a
slow transition in one room never holds up another. The SDK runs its own
background work as detached tasks, so connect, query, the message reader and
disconnect may each run in a different task on the same event loop.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import traceback
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from band.core.exceptions import RoomClosingError
from band.core.retention import (
    ReleaseCause,
    ReleaseOutcome,
    RetainedRoom,
    RoomRetention,
    RoomRetentionConfig,
)
from band.core.turn_lifecycle import TURN_SETTLE_GRACE_S
from band.integrations.claude_sdk.mcp_startup import (
    ClaudeMcpStartupError,
    McpConfigFile,
    McpServerResolver,
    await_mcp_ready,
    freeze_external_servers,
    is_safe_startup_error,
    startup_error,
)
from band.workspaces import RoomWorkspaces

try:
    from claude_agent_sdk import (  # type: ignore[import-not-found]
        ClaudeAgentOptions,
        ClaudeSDKClient,
        CLIConnectionError,
        ResultMessage,
    )
    from claude_agent_sdk._internal.transport import Transport
    from claude_agent_sdk.types import (  # type: ignore[import-not-found]
        CanUseTool,
        McpServerConfig,
        Message,
    )

    from band.integrations.claude_sdk.custody import ConfirmedCloseTransport
    from band.integrations.claude_sdk.transport import create_transport

    _CLAUDE_SDK_AVAILABLE = True
except ImportError:
    _CLAUDE_SDK_AVAILABLE = False

logger = logging.getLogger(__name__)

# A room's CLI starts as an owned acquisition that its turn waits on for at
# most ``transition_timeout_s``; the SDK waits at least this long for the CLI
# to initialize, so a shorter budget would discard healthy starts.
CLI_STARTUP_TIMEOUT_S = 60.0


def default_claude_retention() -> RoomRetentionConfig:
    """Claude's residency settings: a CLI start may take ``CLI_STARTUP_TIMEOUT_S``."""
    return RoomRetentionConfig(transition_timeout_s=CLI_STARTUP_TIMEOUT_S)


class ClaudeSessionManagerStoppedError(RoomClosingError):
    """A session was requested from a manager that ``stop()`` shut down."""


@dataclass(frozen=True)
class StreamEnd:
    """The CLI's output ended, cleanly or with ``error``."""

    error: Exception | None


class TurnStream:
    """One turn's share of its room's single CLI reader.

    The end of the CLI's output stays visible to every later read, so a
    timed-out turn's drain never waits on a stream that already ended.
    """

    def __init__(self) -> None:
        self._messages: asyncio.Queue[Message | StreamEnd] = asyncio.Queue()
        self.completed = False

    def deliver(self, message: Message) -> None:
        self._messages.put_nowait(message)
        if isinstance(message, ResultMessage):
            # The turn reads nothing past its result, so it stops listening here.
            self.completed = True

    def end(self, error: Exception | None) -> None:
        self._messages.put_nowait(StreamEnd(error))

    async def until_result(self) -> AsyncIterator[Message]:
        """Messages up to and including the turn's ``ResultMessage``; the
        stream's end stops early, re-raising a read failure."""
        while not isinstance(item := await self._messages.get(), StreamEnd):
            yield item
            if isinstance(item, ResultMessage):
                return
        self._messages.put_nowait(item)
        if item.error is not None:
            raise item.error


def _start_failure(error: Exception) -> BaseException:
    """The failure a failed ``ClaudeSDKClient.connect`` reports for its start.

    ``connect`` closes the CLI before re-raising a failed start, so a close
    that fails there raises in the start's place, with the start's own
    failure as its context.
    """
    raised_by_close = any(
        frame.f_code is ClaudeSDKClient.disconnect.__code__
        for frame, _ in traceback.walk_tb(error.__traceback__)
    )
    if raised_by_close and isinstance(error.__context__, Exception):
        return error.__context__
    return error


class _StartupReplaced(Exception):
    """The Band endpoint changed before this connection was ready."""


class ClaudeRoomSession:
    """A room's Claude CLI session and everything it keeps until closure.

    ``client`` and ``transport`` are retained before connect starts and
    cleared only once closing them succeeds. ``session_id`` is the native
    conversation the next process resumes; final disposal erases it, and the
    crash policy (``recover(forget_session=True)``) drops it once the crashed
    process is closed.
    """

    def __init__(
        self, manager: ClaudeSessionManager, room_id: str, retention: RoomRetention
    ) -> None:
        self.room_id = room_id
        self.session_id: str | None = None
        self.client: ClaudeSDKClient | None = None
        self.transport: Transport | None = None
        self._manager = manager
        self._reader: asyncio.Task[None] | None = None
        self._turn: TurnStream | None = None
        self._forget_session = False
        self._config_file: McpConfigFile | None = None
        self._protected_servers: dict[str, McpServerConfig] | None = None
        self._acquisition: asyncio.Task[None] | None = None
        self.retained: RetainedRoom = retention.register(
            room_id, acquire=self.acquire, release=self.release, dispose=self.dispose
        )

    @property
    def generation(self) -> int:
        return self.retained.generation

    @property
    def holds_cli(self) -> bool:
        return self.client is not None

    def speaks_for(self, generation: int) -> bool:
        """Whether a callback from residency ``generation`` may still act.

        Room id plus generation alone would collide after a rejoin, so the
        callback also holds this session object, which a rejoin replaces.
        """
        return self.retained.has_authority(generation)

    @asynccontextmanager
    async def using(
        self, prepare: Callable[[], Awaitable[None]] | None = None
    ) -> AsyncIterator[ClaudeSDKClient]:
        """Lease the room's resident client for one accepted operation.

        ``prepare`` binds the operation before a CLI is started for it.
        """
        try:
            async with self.retained.use(prepare):
                if self.client is None:
                    raise RuntimeError(
                        f"Room {self.room_id} is resident without a client"
                    )
                yield self.client
        except asyncio.CancelledError:
            acquisition = self._acquisition
            if (
                self._manager._resolve_mcp_servers is not None
                and acquisition is not None
                and (
                    acquisition.cancelling()
                    or not self.retained.has_authority(self.generation)
                )
            ):
                # The turn's runtime owner still answers for preparation until
                # its cancelled callback and failed launch finish settling.
                await asyncio.wait({acquisition})
            raise

    @contextmanager
    def listening(self) -> Iterator[TurnStream]:
        """Route the reader's messages to one turn until it stops listening."""
        if self._turn is not None:
            raise RuntimeError(f"Room {self.room_id} already has a listening turn")
        stream = self._turn = TurnStream()
        try:
            yield stream
        finally:
            self._turn = None

    def adopt(self, session_id: str | None) -> None:
        """Resume the conversation a bootstrapping room persisted, unless a
        CLI is already live."""
        if session_id is not None and self.client is None:
            self.session_id = session_id

    def recover(self, reason: str, *, forget_session: bool = False) -> bool:
        """Retire the resident CLI through the room's owner.

        It closes once no operation leases the room; until then its callbacks
        lose authority. ``forget_session`` is the crash policy: the next
        process starts a new conversation.
        """
        return self._retire(self.generation, reason, forget_session=forget_session)

    def _retire(self, generation: int, reason: str, *, forget_session: bool) -> bool:
        if not self.retained.recover(generation=generation, reason=reason):
            return False
        self._forget_session = self._forget_session or forget_session
        return True

    def retire_outdated(self) -> None:
        """Compare only the protected servers: reuse never resolves external ones."""
        changed = False
        try:
            if self.client is not None or self._config_file is not None:
                held: object = self._protected_servers
                if (
                    self._manager._resolve_mcp_servers is None
                    and self.client is not None
                ):
                    held = self.client.options.mcp_servers
                current = (
                    self._manager.protected_servers(self.room_id)
                    if self._manager._resolve_mcp_servers is not None
                    else self._manager.mcp_servers(self.room_id)
                )
                changed = bool(held != current)
        except Exception as error:  # noqa: BLE001 -- comparisons can run callback code
            raise startup_error(
                "reservation", "the room's protected servers could not be read", error
            ) from None
        self._manager.require_admission()
        if changed:
            logger.info("Room %s: MCP servers changed; replacing its CLI", self.room_id)
            self.recover("Band MCP servers changed")

    async def acquire(self, generation: int, resume: bool) -> None:
        """Resolve and ready each new connection under the retained acquisition."""
        self._acquisition = asyncio.current_task()
        try:
            while True:
                try:
                    await self._connect(generation, resume=self.session_id)
                except _StartupReplaced:
                    # Close the obsolete launch before resolving a fresh snapshot.
                    await self._close()
                    self._check_admitted(generation)
                    continue
                except Exception as error:
                    if (
                        isinstance(error, ClaudeMcpStartupError)
                        and (
                            not is_safe_startup_error(error)
                            or not error.resume_eligible
                        )
                    ) or not self._may_start_fresh(resume):
                        raise
                    logger.warning(
                        "Room %s: Session resume failed (session_id=%s): %s. "
                        "Creating new session",
                        self.room_id,
                        self.session_id,
                        error,
                    )
                    await self._connect(generation, resume=None)
                    self.session_id = None
                return
        finally:
            self._acquisition = None

    def _may_start_fresh(self, resume: bool) -> bool:
        """Only a failed native start may discard history, after its child closes."""
        return (
            not resume
            and self.session_id is not None
            and not self.retained.closing
            and self.client is None
        )

    def _check_admitted(self, generation: int) -> None:
        """Callback-free fencing, including cancellation a callback swallowed."""
        try:
            self._manager.require_admission()
        except ClaudeSessionManagerStoppedError:
            raise asyncio.CancelledError from None
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            raise asyncio.CancelledError from None
        if self.retained.closing:
            if self._manager._resolve_mcp_servers is None:
                self.retained.require_admission()
            raise startup_error("retirement", "the room's startup was retired")
        if (
            self._manager._resolve_mcp_servers is not None
            and not self.retained.has_authority(generation)
        ):
            raise asyncio.CancelledError from None

    def _check_current(self, generation: int) -> None:
        """Materialize comparisons before the final callback-free guard."""
        changed = False
        if self._manager._resolve_mcp_servers is not None:
            try:
                changed = bool(
                    self._protected_servers
                    != self._manager.protected_servers(self.room_id)
                )
            except Exception as error:  # noqa: BLE001 -- a protected-server factory
                raise startup_error(
                    "reservation",
                    "the room's protected servers could not be read",
                    error,
                ) from None
        self._check_admitted(generation)
        if changed:
            raise _StartupReplaced

    async def release(self, cause: ReleaseCause) -> ReleaseOutcome:
        """Close the room's CLI for recovery or final cleanup."""
        await self._close()
        if self._forget_session:
            self.session_id = None
            self._forget_session = False
        logger.debug("Room %s: Claude CLI closed (%s)", self.room_id, cause)
        return ReleaseOutcome.RELEASED

    async def dispose(self) -> None:
        """Erase the room's continuation once its final release is confirmed."""
        self.session_id = None
        self._manager.discard(self)

    async def _connect(self, generation: int, *, resume: str | None) -> None:
        # A failed removal can leave only a file. Retry that obligation before
        # resolving or allocating anything for the next attempt.
        if self._config_file is not None:
            await self._close()
        self._check_admitted(generation)
        stage = "reservation"
        native_start = False
        resolving = self._manager._resolve_mcp_servers is not None
        ready_servers: frozenset[str] = frozenset()
        try:
            if resolving:
                workspace = self._manager.workspace(self.room_id)
                protected = self._manager.protected_servers(self.room_id)
                self._protected_servers = protected
                self._check_current(generation)
                stage = "resolution"
                resolver = self._manager._resolve_mcp_servers
                assert resolver is not None
                try:
                    resolved = await resolver(workspace)
                except Exception as error:  # noqa: BLE001 -- callback errors are never trusted
                    raise startup_error(
                        "resolution", "resolve_mcp_servers failed", error
                    ) from None
                external = freeze_external_servers(resolved, protected)
                ready_servers = frozenset({*external, *protected})
                self._check_current(generation)
                stage = "launch"
                self._config_file = McpConfigFile.allocate()
                self._config_file.write({**external, **protected})
                options = self._manager.options_for(
                    self,
                    generation,
                    resume=resume,
                    mcp_servers=str(self._config_file.path),
                )
            else:
                options = self._manager.options_for(self, generation, resume=resume)
            self._check_current(generation)
            self.transport = transport = ConfirmedCloseTransport(
                create_transport(options)
            )
            self.client = client = ClaudeSDKClient(options=options, transport=transport)
            # Retain both before connect: SDK disconnect cannot close a child
            # when startup fails before its Query is constructed.
            native_start = True
            await client.connect()
            native_start = False
            self._check_current(generation)
            if resolving:
                stage = "status"
                try:
                    await await_mcp_ready(client.get_mcp_status, ready_servers)
                except ClaudeMcpStartupError:
                    self._check_current(generation)
                    raise
                self._check_current(generation)
        except asyncio.CancelledError:
            if resolving:
                await self._close_failed_start()
            raise asyncio.CancelledError from None
        except _StartupReplaced:
            raise
        except Exception as error:
            await self._close_failed_start()
            self._check_admitted(generation)
            failure = _start_failure(error)
            if not resolving:
                if failure is error:
                    raise
                raise failure
            if isinstance(error, ClaudeMcpStartupError) and is_safe_startup_error(
                error
            ):
                raise
            raise startup_error(
                stage,
                "the Claude CLI did not start"
                if native_start
                else "the Claude session could not be constructed",
                failure,
                resume_eligible=native_start,
            ) from None
        self._reader = asyncio.create_task(self._read(client, generation))
        logger.info(
            "Room %s: Claude session started (resume=%s)", self.room_id, options.resume
        )

    async def _close_failed_start(self) -> None:
        """Close a CLI whose start failed; a refused close keeps the handles
        for recovery to retry."""
        try:
            await self._close()
        except Exception:
            logger.warning(
                "Room %s: closing a failed Claude start failed; recovery retries it",
                self.room_id,
                exc_info=self._manager._resolve_mcp_servers is None,
            )

    async def _close(self) -> None:
        """Keep every unresolved child or private-file obligation for a retry."""
        try:
            if self.client is not None or self.transport is not None:
                try:
                    if self.client is not None:
                        await self.client.disconnect()
                finally:
                    if self.transport is not None:
                        await self.transport.close()
                if self._reader is not None:
                    await self._reader
                self.client = self.transport = self._reader = None
        except Exception as error:
            if self._manager._resolve_mcp_servers is None:
                raise
            raise startup_error(
                "cleanup", "the Claude CLI could not be confirmed closed", error
            ) from None
        if self._config_file is not None:
            self._config_file.release()
            self._config_file = None
        self._protected_servers = None

    async def _read(self, client: ClaudeSDKClient, generation: int) -> None:
        """The CLI's only reader, across turns, from connect to disconnect."""
        error: Exception | None = None
        try:
            async for message in client.receive_messages():
                if (turn := self._listener(generation)) is not None:
                    turn.deliver(message)
                else:
                    logger.debug(
                        "Room %s: %s arrived outside any turn; not attributed",
                        self.room_id,
                        type(message).__name__,
                    )
        except Exception as failure:  # noqa: BLE001 -- the listening turn re-raises it
            error = failure
        if (turn := self._listener(generation)) is not None:
            turn.end(error)
            return
        # No turn will read this end. A dead process gets the crash policy, as
        # inside a turn; a read failure from a running CLI keeps the session.
        crashed = error is None or isinstance(error, CLIConnectionError)
        reason = "CLI output ended outside a turn" + (f": {error}" if error else "")
        if self._retire(generation, reason, forget_session=crashed):
            logger.warning(
                "Room %s: %s; retiring its CLI", self.room_id, reason, exc_info=error
            )
        else:
            logger.debug("Room %s: CLI output ended (%s)", self.room_id, error)

    def _listener(self, generation: int) -> TurnStream | None:
        turn = self._turn
        if turn is None or turn.completed or not self.retained.may_finish(generation):
            return None
        return turn


class ClaudeSessionManager:
    """
    Manages one Claude CLI session per chat room.

    Each room's session is created on its first leased operation, kept across
    messages, and owned by one ``RoomRetention`` that settles and closes every
    room independently. ``stop()`` is final: the adapter builds a new manager
    to start again.

    Example:
        manager = ClaudeSessionManager(base_options)
        session = manager.room("room-123")
        async with session.using() as client:
            with session.listening() as stream:
                await client.query("Hello")
                async for msg in stream.until_result():
                    logger.info("%s", msg)
        await manager.cleanup_session("room-123")
        await manager.stop()
    """

    def __init__(
        self,
        base_options: ClaudeAgentOptions,
        can_use_tool_factory: Callable[[ClaudeRoomSession, int], CanUseTool]
        | None = None,
        mcp_servers_factory: Callable[[str], dict[str, McpServerConfig]] | None = None,
        workspaces: RoomWorkspaces | None = None,
        retention: RoomRetentionConfig | None = None,
        settle_grace_s: float = TURN_SETTLE_GRACE_S,
        on_disposed: Callable[[str], None] | None = None,
        resolve_mcp_servers: McpServerResolver | None = None,
    ) -> None:
        """
        Initialize session manager.

        Args:
            base_options: Base ClaudeAgentOptions shared by every room session.
            can_use_tool_factory: Optional factory for a ``can_use_tool``
                callback bound to one room session and its residency
                generation.
            mcp_servers_factory: Optional factory that returns a room's
                ``mcp_servers``, replacing ``base_options.mcp_servers``.
            workspaces: Adapter-local claims; omit to preserve base_options.cwd.
            retention: Residency settings, including the transition reporting
                budget, which defaults to ``CLI_STARTUP_TIMEOUT_S``; idle
                release stays with the adapter's validation.
            settle_grace_s: How long a leave or stop lets a room's turn wind
                down on its own before cancelling it.
            on_disposed: Called with a room's id once its final release is
                confirmed, even if the leave that started it stopped waiting.
            resolve_mcp_servers: Complete external servers for each new connection;
                never called on reuse, and never allowed to replace Band's servers.
        """
        self.base_options = base_options
        self._can_use_tool_factory = can_use_tool_factory
        self._mcp_servers_factory = mcp_servers_factory
        self._workspaces = workspaces
        self._on_disposed = on_disposed
        self._resolve_mcp_servers = resolve_mcp_servers
        self._retention = RoomRetention(
            retention or default_claude_retention(),
            settle_grace_s=settle_grace_s,
        )
        self._rooms: dict[str, ClaudeRoomSession] = {}

    def require_admission(self) -> None:
        if self._retention.stopping:
            raise ClaudeSessionManagerStoppedError()

    def close_admission(self) -> None:
        """Fence new work now; the retained owner settles it through ``stop``."""
        self._retention.stopping = True
        self._retention.reschedule()

    def owns_task(self, task: asyncio.Task[Any] | None) -> bool:
        return any(session.retained.enrolls(task) for session in self._rooms.values())

    def room(self, room_id: str) -> ClaudeRoomSession:
        """The room's session record, registered on first use."""
        self.require_admission()
        if (session := self._rooms.get(room_id)) is None:
            session = self._rooms[room_id] = ClaudeRoomSession(
                self, room_id, self._retention
            )
        return session

    def session(self, room_id: str) -> ClaudeRoomSession | None:
        """The room's session record, if any; never registers one."""
        return self._rooms.get(room_id)

    def mcp_servers(self, room_id: str) -> dict[str, McpServerConfig] | str | Path:
        if self._mcp_servers_factory is None:
            return self.base_options.mcp_servers
        return self._mcp_servers_factory(room_id)

    def protected_servers(self, room_id: str) -> dict[str, McpServerConfig]:
        """Band's protected servers, separate from the resolver's complete result."""
        return (
            {}
            if self._mcp_servers_factory is None
            else self._mcp_servers_factory(room_id)
        )

    def workspace(self, room_id: str) -> str:
        if self._workspaces is not None:
            return self._workspaces.claim(room_id)
        return str(self.base_options.cwd or Path.cwd())

    def options_for(
        self,
        session: ClaudeRoomSession,
        generation: int,
        *,
        resume: str | None,
        mcp_servers: str | None = None,
    ) -> ClaudeAgentOptions:
        """``base_options`` with one room session's overrides applied.

        ``dataclasses.replace()`` shallow-copies every base field.
        """
        overrides: dict[str, Any] = {
            "mcp_servers": self.mcp_servers(session.room_id)
            if mcp_servers is None
            else mcp_servers
        }
        if self._workspaces is not None:
            overrides["cwd"] = self._workspaces.claim(session.room_id)
        if resume:
            overrides["resume"] = resume
        if self._can_use_tool_factory:
            overrides["can_use_tool"] = self._can_use_tool_factory(session, generation)
        return dataclasses.replace(self.base_options, **overrides)

    def discard(self, session: ClaudeRoomSession) -> None:
        """Forget a disposed room and release its workspace claim."""
        if self._rooms.get(session.room_id) is session:
            del self._rooms[session.room_id]
            if self._on_disposed is not None:
                self._on_disposed(session.room_id)
        if self._workspaces is not None:
            self._workspaces.release(session.room_id)

    async def cleanup_session(self, room_id: str) -> None:
        """Settle the room's work, close its CLI and erase its continuation.

        Called when the agent leaves the room. Admission closes at once; a
        failed or unfinished cleanup stays owned and a retry joins it.
        """
        if (session := self._rooms.get(room_id)) is not None:
            await session.retained.dispose()

    async def stop(
        self, *, report_timeout_s: float | None | Literal["configured"] = "configured"
    ) -> None:
        """Dispose every room; a failed or unfinished room stays owned for a
        retry, and the manager never serves again."""
        await self._retention.stop(report_timeout_s=report_timeout_s)

    def has_session(self, room_id: str) -> bool:
        """Whether the room has a CLI process held."""
        session = self._rooms.get(room_id)
        return session is not None and session.holds_cli

    def get_session_count(self) -> int:
        """Number of rooms holding a CLI process."""
        return len(self.get_active_rooms())

    def get_active_rooms(self) -> list[str]:
        """Room IDs holding a CLI process."""
        return [room for room, session in self._rooms.items() if session.holds_cli]
