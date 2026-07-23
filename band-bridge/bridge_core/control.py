"""Agent control-signal handling (interrupt / stop / play) for the bridge.

Split out of ``AgentRunner``: the runner owns forwarding, rehydration, and the
per-room in-flight task registry; this module owns only the control-signal
concern — correlation-id dedup, target-room resolution, and mode dispatch —
delegating the actual cancel / nudge / room-list operations back to the runner.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import TYPE_CHECKING

from band.client.streaming import AgentControlPayload

if TYPE_CHECKING:
    from .bridge import AgentRunner

logger = logging.getLogger(__name__)

_CONTROL_DEDUP_MAX_SIZE = 256


class ControlSignalHandler:
    """Applies ``agent.control`` signals to a runner's in-flight forwards.

    The bridge holds no Band lifecycle logic, so ``interrupt`` and ``stop`` are
    handled identically: cancel whatever forward task is currently in flight
    for the target room(s), if any. There is nothing to do if no forward is in
    flight — the interrupt-vs-stop message-lifecycle distinction (consume vs.
    leave-for-replay) is already handled downstream by the container via its
    own ``/next`` claim check. ``play`` proactively nudges the room(s) via
    ``/next`` so a queued message is picked up without waiting for the next
    natural bridge event.
    """

    def __init__(self, runner: AgentRunner) -> None:
        self._runner = runner
        # Control-signal dedup. The server does not deduplicate agent.control
        # pushes, so a stale duplicate could otherwise reach out and cancel
        # whatever unrelated new task now occupies a room slot. Bounded LRU;
        # only touched from the WebSocket receive task (same as AgentRuntime's
        # _seen_control_ids on the long-running SDK path).
        self._seen_control_ids: OrderedDict[str, bool] = OrderedDict()

    async def handle(self, payload: AgentControlPayload) -> None:
        """Apply an ``agent.control`` signal to in-flight forwards.

        Routing mirrors ``AgentRuntime.handle_control``: a ``room_id`` targets
        that room only; ``scope == "agent"`` with no ``room_id`` fans out to
        all of this agent's rooms; any other combination is a no-op. Dedupes on
        ``correlation_id`` (the server does not).
        """
        if self._is_duplicate(payload):
            return

        room_ids = await self._resolve_rooms(payload)
        if room_ids is None:
            return

        agent_id = self._runner.agent_id
        logger.info(
            "Agent %s: applying control mode=%s scope=%s to %d room(s) "
            "(correlation_id=%s)",
            agent_id,
            payload.mode,
            payload.scope,
            len(room_ids),
            payload.correlation_id,
        )

        match payload.mode:
            case "interrupt" | "stop":
                for room_id in room_ids:
                    self._runner._cancel_active_forward(room_id)
            case "play":
                # Nudge in the background so a following stop/interrupt isn't
                # blocked behind this /next + forward on the WS receive task.
                self._runner._spawn_play_nudge(room_ids)
            case _:
                logger.warning(
                    "Agent %s: ignoring control signal with unknown mode=%s",
                    agent_id,
                    payload.mode,
                )

    def _is_duplicate(self, payload: AgentControlPayload) -> bool:
        """Return True if this signal's ``correlation_id`` was already handled.

        Records unseen ids in a bounded LRU. Signals without a
        ``correlation_id`` are never treated as duplicates (the server omits
        it on some pushes) but are logged.
        """
        agent_id = self._runner.agent_id
        cid = payload.correlation_id
        if cid is None:
            logger.debug(
                "Agent %s: control signal mode=%s has no correlation_id; not deduped",
                agent_id,
                payload.mode,
            )
            return False

        if cid in self._seen_control_ids:
            logger.debug(
                "Agent %s: duplicate control signal %s ignored",
                agent_id,
                cid,
            )
            return True

        self._seen_control_ids[cid] = True
        self._seen_control_ids.move_to_end(cid)
        if len(self._seen_control_ids) > _CONTROL_DEDUP_MAX_SIZE:
            self._seen_control_ids.popitem(last=False)
        return False

    async def _resolve_rooms(self, payload: AgentControlPayload) -> list[str] | None:
        """Resolve the signal's target room ids, or None for a no-op.

        A ``room_id`` targets that room; ``scope == "agent"`` with no
        ``room_id`` fans out to all of the agent's rooms (possibly empty); any
        other combination returns None so the caller skips dispatch.
        """
        if payload.room_id is not None:
            return [payload.room_id]
        if payload.scope == "agent":
            return await self._runner._fetch_existing_rooms()
        logger.warning(
            "Agent %s: control signal scope=%s with no room_id; no-op",
            self._runner.agent_id,
            payload.scope,
        )
        return None
