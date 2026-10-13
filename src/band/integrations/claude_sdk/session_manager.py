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
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from band.core.exceptions import RoomClosingError
from band.core.retention import (
    ReleaseCause,
    ReleaseOutcome,
    Residency,
    RetainedRoom,
    RoomRetention,
    RoomRetentionConfig,
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

    from band.integrations.claude_sdk.transport import create_transport

    _CLAUDE_SDK_AVAILABLE = True
except ImportError:
    _CLAUDE_SDK_AVAILABLE = False

logger = logging.getLogger(__name__)

# A room's CLI starts as an owned acquisition that its turn waits on for at
# most ``transition_timeout_s``; the SDK waits at least this long for the CLI
# to initialize, so a shorter budget would discard healthy starts.
CLI_STARTUP_TIMEOUT_S = 60.0


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
        self.retained: RetainedRoom = retention.register(
            room_id, acquire=self.acquire, release=self.release, dispose=self.dispose
        )

    @property
    def generation(self) -> int:
        return self.retained.snapshot.generation

    @property
    def holds_cli(self) -> bool:
        return self.client is not None

    @property
    def closing(self) -> bool:
        """Whether the room's final cleanup has begun; it never serves again."""
        return self._manager.stopping or self.retained.snapshot.disposing

    def speaks_for(self, generation: int) -> bool:
        """Whether a callback from residency ``generation`` may still act.

        Room id plus generation alone would collide after a rejoin, so the
        callback also holds this session object, which a rejoin replaces.
        """
        snapshot = self.retained.snapshot
        return (
            snapshot.generation == generation
            and snapshot.state == Residency.RESIDENT
            and snapshot.pending_release is None
        )

    @asynccontextmanager
    async def using(
        self, prepare: Callable[[], Awaitable[None]] | None = None
    ) -> AsyncIterator[ClaudeSDKClient]:
        """Lease the room's resident client for one accepted operation.

        ``prepare`` binds the operation before a CLI is started for it.
        """
        async with self.retained.use(prepare):
            if self.client is None:
                raise RuntimeError(f"Room {self.room_id} is resident without a client")
            yield self.client

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
        """A CLI dialing MCP servers the room no longer has resumes on the
        current ones."""
        if self.client is None:
            return
        if self.client.options.mcp_servers != self._manager.mcp_servers(self.room_id):
            logger.info("Room %s: MCP servers changed; replacing its CLI", self.room_id)
            self.recover("Band MCP servers changed")

    async def acquire(self, generation: int, resume: bool) -> None:
        """Start the room's CLI. ``resume`` restores the retained session
        strictly; otherwise an unresumable earlier session gives way to a new
        one, the bootstrap and crash policy, unless the room began closing."""
        try:
            await self._connect(generation, resume=self.session_id)
        except Exception as error:
            if not self._may_start_fresh(resume):
                raise
            logger.warning(
                "Room %s: Session resume failed (session_id=%s): %s. "
                "Creating new session",
                self.room_id,
                self.session_id,
                error,
            )
            # The conversation is dropped only once a new one is running.
            await self._connect(generation, resume=None)
            self.session_id = None

    def _may_start_fresh(self, resume: bool) -> bool:
        """Whether a failed resume may give way to a new session: never on a
        strict resume or once closing, and not while the failed CLI, which
        could not be closed, is still held for recovery."""
        return (
            not resume
            and self.session_id is not None
            and not self.closing
            and self.client is None
        )

    async def release(self, cause: ReleaseCause) -> ReleaseOutcome:
        """Close the room's CLI for recovery or final cleanup.

        Disconnect can cancel async managed hooks and background tasks the
        CLI does not report, so an idle release is refused before anything
        closes.
        """
        if cause == ReleaseCause.IDLE:
            return ReleaseOutcome.BUSY
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
        options = self._manager.options_for(self, generation, resume=resume)
        transport = create_transport(options)
        client = ClaudeSDKClient(options=options, transport=transport)
        # Retain both before connect: SDK disconnect cannot close a subprocess
        # when startup fails before its Query is constructed.
        self.client, self.transport = client, transport
        try:
            await client.connect()
        except Exception:
            await self._close()
            raise
        self._reader = asyncio.create_task(self._read(client, generation))
        logger.info(
            "Room %s: Claude session started (resume=%s)", self.room_id, options.resume
        )

    async def _close(self) -> None:
        """Close the retained CLI; a failure keeps every handle for a retry."""
        if self.client is None or self.transport is None:
            return
        try:
            await self.client.disconnect()
        finally:
            await self.transport.close()
        if self._reader is not None:
            await self._reader
        self.client = self.transport = self._reader = None

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
        if turn is None or turn.completed or not self.speaks_for(generation):
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
        """
        self.base_options = base_options
        self._can_use_tool_factory = can_use_tool_factory
        self._mcp_servers_factory = mcp_servers_factory
        self._workspaces = workspaces
        self._retention = RoomRetention(
            retention or RoomRetentionConfig(transition_timeout_s=CLI_STARTUP_TIMEOUT_S)
        )
        self._rooms: dict[str, ClaudeRoomSession] = {}

    @property
    def stopping(self) -> bool:
        return self._retention.stopping

    def room(self, room_id: str) -> ClaudeRoomSession:
        """The room's session record, registered on first use."""
        if self.stopping:
            raise ClaudeSessionManagerStoppedError()
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

    def options_for(
        self, session: ClaudeRoomSession, generation: int, *, resume: str | None
    ) -> ClaudeAgentOptions:
        """``base_options`` with one room session's overrides applied.

        ``dataclasses.replace()`` shallow-copies every base field.
        """
        overrides: dict[str, Any] = {"mcp_servers": self.mcp_servers(session.room_id)}
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
        if self._workspaces is not None:
            self._workspaces.release(session.room_id)

    async def cleanup_session(self, room_id: str) -> None:
        """Settle the room's work, close its CLI and erase its continuation.

        Called when the agent leaves the room. Admission closes at once; a
        failed or unfinished cleanup stays owned and a retry joins it.
        """
        if (session := self._rooms.get(room_id)) is not None:
            await session.retained.dispose()

    async def stop(self) -> None:
        """Dispose every room; a failed or unfinished room stays owned for a
        retry, and the manager never serves again."""
        await self._retention.stop()

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
