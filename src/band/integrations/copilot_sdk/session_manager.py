"""Session manager for Copilot SDK sessions.

Maps each Band room to its own Copilot session so history, memory, and
context stay isolated per room. A single client serves all rooms; sessions
run concurrently across rooms, but calls on the *same* session must never
interleave — the manager owns a per-room turn lock enforcing that.

Concurrency model: all state lives on one asyncio event loop (the SDK's
internal reader threads marshal callbacks back onto it, so there is no
cross-thread state access).
Turns hold the room's lock; ``cleanup_session`` takes the same lock, so a
session is never disconnected under an in-flight turn. Turn locks are never
dropped — every waiter for a room always contends on the same lock object.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Bound on abort/disconnect RPCs against a possibly-dead runtime.
_EVICT_OP_TIMEOUT_S = 5.0


class CopilotSessionManager:
    """Owns the Copilot client, per-room turn locks, and the session registry.

    Session *creation policy* (resume vs create, tool bridging, system
    message) belongs to the adapter; the manager only stores the result.
    """

    def __init__(self, client: Any, *, owns_client: bool = True) -> None:
        """Wrap a Copilot client.

        A borrowed client (``owns_client=False``) is shared with other
        managers: ``stop()`` releases this manager's sessions but never
        stops the client itself — its owner does.
        """
        self._client = client
        self._owns_client = owns_client
        self._sessions: dict[str, Any] = {}
        self._turn_locks: dict[str, asyncio.Lock] = {}
        self._start_lock = asyncio.Lock()

    @property
    def client(self) -> Any:
        return self._client

    async def ensure_started(self) -> None:
        """Start — or revive — the underlying client.

        Delegates liveness to the client itself: ``CopilotClient.start()``
        is a no-op while connected and respawns the runtime after a crash
        (its state flips to "disconnected"), so no flag is cached here —
        a cached flag would skip exactly the restart that heals a dead CLI.
        """
        async with self._start_lock:
            await self._client.start()

    def turn_lock(self, room_id: str) -> asyncio.Lock:
        """Return the room's lock serializing turns and cleanup on its session."""
        return self._turn_locks.setdefault(room_id, asyncio.Lock())

    def get_session(self, room_id: str) -> Any | None:
        """Return the room's session, or None if it has none."""
        return self._sessions.get(room_id)

    def store_session(self, room_id: str, session: Any) -> None:
        """Register the room's session."""
        self._sessions[room_id] = session
        logger.info("Room %s: Copilot session registered", room_id)

    async def cleanup_session(self, room_id: str) -> None:
        """Disconnect and drop the room's session (disk state is preserved).

        Takes the room's turn lock, so an in-flight turn is never
        disconnected under itself.
        """
        async with self.turn_lock(room_id):
            session = self._sessions.pop(room_id, None)
            if session is not None:
                try:
                    await session.disconnect()
                except Exception as exc:
                    logger.warning(
                        "Room %s: session disconnect failed: %s", room_id, exc
                    )
                logger.info("Room %s: Copilot session cleaned up", room_id)

    async def evict_session(self, room_id: str) -> None:
        """Abort and drop the room's session after a failed turn.

        The caller already holds the room's turn lock. Abort stops any
        work the runtime is still executing for the timed-out/failed turn
        (so stale tool calls can't fire later); each RPC is timeout-bounded
        because the runtime may already be dead. Disk state is preserved,
        so the next message can resume the session fresh.
        """
        session = self._sessions.pop(room_id, None)
        if session is not None:
            for close_op in (session.abort, session.disconnect):
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(close_op(), timeout=_EVICT_OP_TIMEOUT_S)
            logger.info("Room %s: Copilot session evicted after failed turn", room_id)

    async def cleanup_all(self) -> None:
        """Disconnect and drop every session, each under its room's lock."""
        for room_id in list(self._sessions):
            await self.cleanup_session(room_id)

    async def stop(self) -> None:
        """Clean up all sessions; stop the client only if this manager owns it.

        Turn locks are deliberately retained — lock identity must stay
        stable for any waiter still queued on a room.
        """
        await self.cleanup_all()
        if self._owns_client:
            try:
                await self._client.stop()
            except Exception as exc:
                logger.warning("Copilot client stop failed: %s", exc)
            logger.info("Copilot client stopped")
