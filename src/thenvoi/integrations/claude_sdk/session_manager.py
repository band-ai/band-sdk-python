"""
Session manager for Claude Agent SDK clients.

Maintains one ClaudeSDKClient instance per Thenvoi chat room to ensure
conversation continuity within each room.

Uses a dedicated background task for all session operations to ensure
connect() and disconnect() are always called from the same task context.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

try:
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions  # type: ignore[import-not-found]
    from claude_agent_sdk.types import CanUseTool  # type: ignore[import-not-found]

    _CLAUDE_SDK_AVAILABLE = True
except ImportError:
    _CLAUDE_SDK_AVAILABLE = False

logger = logging.getLogger(__name__)


@dataclass
class _SessionCommand:
    """Command to be processed by the session manager task."""

    action: str  # "create", "cleanup", "cleanup_all", "invalidate", "stop"
    room_id: str | None = None
    resume_session_id: str | None = None
    result_future: asyncio.Future[Any] | None = None


class ClaudeSessionManager:
    """
    Manages ClaudeSDKClient instances per chat room.

    Each room gets its own ClaudeSDKClient instance to maintain separate
    conversation histories and sessions.

    Key features:
    - Lazy initialization (clients created on first message)
    - Session reuse (same client for all messages in a room)
    - Graceful cleanup on room leave/disconnect
    - All session operations run in a single background task to avoid
      cross-task asyncio issues with connect/disconnect

    Example:
        import logging
        logger = logging.getLogger(__name__)

        manager = ClaudeSessionManager(base_options)
        await manager.start()  # Start background task

        # Get client for room (creates if doesn't exist)
        client = await manager.get_or_create_session("room-123")

        # Use client
        await client.query("Hello")
        async for msg in client.receive_response():
            logger.info("%s", msg)

        # Cleanup when done
        await manager.cleanup_session("room-123")
        await manager.stop()  # Stop background task
    """

    def __init__(
        self,
        base_options: ClaudeAgentOptions,
        can_use_tool_factory: Callable[[str], CanUseTool] | None = None,
    ):
        """
        Initialize session manager.

        Args:
            base_options: Base ClaudeAgentOptions to use for all clients.
                          These options are shared across all room sessions.
            can_use_tool_factory: Optional factory that creates a room-specific
                ``can_use_tool`` callback.  When set, each new session receives
                its own callback bound to the room_id.
        """
        self.base_options = base_options
        self._can_use_tool_factory = can_use_tool_factory
        self._sessions: dict[str, ClaudeSDKClient] = {}
        self._command_queue: asyncio.Queue[_SessionCommand] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._started = False
        logger.info("ClaudeSessionManager initialized")

    async def start(self) -> None:
        """Start the background task that manages all sessions."""
        if self._started:
            return

        self._task = asyncio.create_task(self._run_session_loop())
        self._started = True
        logger.info("ClaudeSessionManager background task started")

    async def stop(self) -> None:
        """Stop the background task and cleanup all sessions."""
        if not self._started:
            return

        # Send stop command
        stop_future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        await self._command_queue.put(
            _SessionCommand(action="stop", result_future=stop_future)
        )

        # Wait for cleanup to complete
        await stop_future

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        self._started = False
        logger.info("ClaudeSessionManager background task stopped")

    async def _run_session_loop(self) -> None:
        """Background task that processes all session commands."""
        logger.debug("Session loop started")

        while True:
            cmd: _SessionCommand | None = None
            try:
                cmd = await self._command_queue.get()

                if cmd.action == "create":
                    client = await self._do_create_session(
                        cmd.room_id, cmd.resume_session_id
                    )
                    if cmd.result_future:
                        cmd.result_future.set_result(client)

                elif cmd.action == "cleanup":
                    await self._do_cleanup_session(cmd.room_id)
                    if cmd.result_future:
                        cmd.result_future.set_result(None)

                elif cmd.action == "invalidate":
                    self._do_invalidate_session(cmd.room_id)
                    if cmd.result_future:
                        cmd.result_future.set_result(None)

                elif cmd.action == "cleanup_all":
                    await self._do_cleanup_all()
                    if cmd.result_future:
                        cmd.result_future.set_result(None)

                elif cmd.action == "stop":
                    await self._do_cleanup_all()
                    if cmd.result_future:
                        cmd.result_future.set_result(None)
                    break

                self._command_queue.task_done()

            except asyncio.CancelledError:
                logger.debug("Session loop cancelled")
                break
            except Exception as e:
                logger.error("Error in session loop: %s", e, exc_info=True)
                if cmd and cmd.result_future and not cmd.result_future.done():
                    cmd.result_future.set_exception(e)

        logger.debug("Session loop exited")

    def _build_options(
        self, room_id: str, resume_session_id: str | None = None
    ) -> ClaudeAgentOptions:
        """Build ``ClaudeAgentOptions`` for a specific room session.

        Uses ``dataclasses.replace()`` to shallow-copy all fields from
        ``base_options``, then applies room-specific overrides.
        """
        overrides: dict[str, Any] = {}

        if resume_session_id:
            overrides["resume"] = resume_session_id

        if self._can_use_tool_factory:
            overrides["can_use_tool"] = self._can_use_tool_factory(room_id)

        return dataclasses.replace(self.base_options, **overrides)

    async def _do_create_session(
        self, room_id: str | None, resume_session_id: str | None
    ) -> ClaudeSDKClient:
        """Create or get session (runs in background task)."""
        if not room_id:
            raise ValueError("room_id is required")

        if room_id not in self._sessions:
            if resume_session_id:
                logger.info(
                    "Resuming session %s for room: %s", resume_session_id, room_id
                )
            else:
                logger.info(
                    "Creating new ClaudeSDKClient session for room: %s", room_id
                )

            options = self._build_options(room_id, resume_session_id)

            # Create new client with options
            client = ClaudeSDKClient(options=options)

            # Connect the client (establishes session with Claude)
            await client.connect()

            # Store for reuse
            self._sessions[room_id] = client

            logger.info(
                "Session created for room %s (total sessions: %s)",
                room_id,
                len(self._sessions),
            )
        else:
            logger.debug("Reusing existing session for room: %s", room_id)

        return self._sessions[room_id]

    def _do_invalidate_session(self, room_id: str | None) -> None:
        """Evict a dead session without calling disconnect() (runs in background task).

        Use this when the CLI process has already terminated — calling
        disconnect() on a dead process would raise or hang.
        """
        if not room_id or room_id not in self._sessions:
            logger.debug("No session to invalidate for room: %s", room_id)
            return

        del self._sessions[room_id]
        logger.info(
            "Invalidated dead session for room %s (remaining sessions: %s)",
            room_id,
            len(self._sessions),
        )

    async def _do_cleanup_session(self, room_id: str | None) -> None:
        """Cleanup single session (runs in background task)."""
        if not room_id or room_id not in self._sessions:
            logger.debug("No session to cleanup for room: %s", room_id)
            return

        logger.info("Cleaning up session for room: %s", room_id)

        try:
            await self._sessions[room_id].disconnect()
            logger.debug("Disconnected client for room %s", room_id)
        except Exception as e:
            logger.warning("Error disconnecting session for room %s: %s", room_id, e)

        del self._sessions[room_id]

        logger.info(
            "Session cleaned up for room %s (remaining sessions: %s)",
            room_id,
            len(self._sessions),
        )

    async def _do_cleanup_all(self) -> None:
        """Cleanup all sessions (runs in background task)."""
        logger.info("Cleaning up all sessions (count: %s)", len(self._sessions))

        room_ids = list(self._sessions.keys())
        for room_id in room_ids:
            await self._do_cleanup_session(room_id)

        logger.info("All sessions cleaned up")

    async def get_or_create_session(
        self, room_id: str, resume_session_id: str | None = None
    ) -> ClaudeSDKClient:
        """
        Get existing ClaudeSDKClient for room or create new one.

        This method is idempotent - calling it multiple times for the same
        room_id returns the same client instance.

        Args:
            room_id: Thenvoi chat room ID (UUID)
            resume_session_id: Optional session ID to resume from a previous
                              session. Only used when creating a new session.

        Returns:
            ClaudeSDKClient instance for this room
        """
        if not self._started:
            await self.start()

        result_future: asyncio.Future[ClaudeSDKClient] = (
            asyncio.get_running_loop().create_future()
        )
        await self._command_queue.put(
            _SessionCommand(
                action="create",
                room_id=room_id,
                resume_session_id=resume_session_id,
                result_future=result_future,
            )
        )
        return await result_future

    async def cleanup_session(self, room_id: str) -> None:
        """
        Disconnect and remove session for a room.

        This should be called when:
        - Agent is removed from the room
        - Room is deleted
        - Adapter is shutting down

        Args:
            room_id: Thenvoi chat room ID
        """
        if not self._started:
            return

        result_future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        await self._command_queue.put(
            _SessionCommand(
                action="cleanup",
                room_id=room_id,
                result_future=result_future,
            )
        )
        await result_future

    async def invalidate_session(self, room_id: str) -> None:
        """
        Evict a dead session without calling disconnect().

        Use this when the CLI subprocess has already exited — calling
        disconnect() on a dead process would raise or hang.  After
        invalidation, ``get_or_create_session`` will create a fresh client.

        Args:
            room_id: Thenvoi chat room ID
        """
        if not self._started:
            return

        result_future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        await self._command_queue.put(
            _SessionCommand(
                action="invalidate",
                room_id=room_id,
                result_future=result_future,
            )
        )
        await result_future

    async def cleanup_all(self) -> None:
        """
        Disconnect all sessions.

        This should be called when the adapter is shutting down to ensure
        all Claude SDK clients are properly disconnected.
        """
        if not self._started:
            return

        result_future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        await self._command_queue.put(
            _SessionCommand(
                action="cleanup_all",
                result_future=result_future,
            )
        )
        await result_future

    def has_session(self, room_id: str) -> bool:
        """Check if session exists for room."""
        return room_id in self._sessions

    def get_session_count(self) -> int:
        """Get number of active sessions."""
        return len(self._sessions)

    def get_active_rooms(self) -> list[str]:
        """Get list of room IDs with active sessions."""
        return list(self._sessions.keys())
