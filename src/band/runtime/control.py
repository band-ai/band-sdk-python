"""Routing ``agent.control`` signals (interrupt/stop/play) to executions."""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any

from band.client.streaming import ControlMode
from band.runtime.execution import Execution

if TYPE_CHECKING:
    from band.client.streaming import AgentControlPayload

logger = logging.getLogger(__name__)

ControlCallback = Callable[[str, ControlMode], Awaitable[None]]

MAX_SEEN_CONTROL_IDS = 256


class ControlDeduplicator:
    """Drops repeated signals by correlation id; the server does not.

    Bounded LRU, only touched from the WebSocket receive task.
    """

    def __init__(self) -> None:
        self._seen: OrderedDict[str, None] = OrderedDict()

    def first_sighting(self, payload: AgentControlPayload) -> bool:
        cid = payload.correlation_id
        if cid is None:
            logger.debug(
                "Control signal mode=%s has no correlation_id; not deduped",
                payload.mode,
            )
            return True
        if cid in self._seen:
            logger.debug("Duplicate control signal %s ignored", cid)
            return False
        self._seen[cid] = None
        if len(self._seen) > MAX_SEEN_CONTROL_IDS:
            self._seen.popitem(last=False)
        return True


def control_targets(
    payload: AgentControlPayload, executions: Mapping[str, Execution]
) -> list[Execution]:
    """A room-addressed signal reaches that room; an agent-scoped one, all."""
    match payload.room_id, payload.scope:
        case None, "agent":
            return list(executions.values())
        case None, scope:
            logger.warning("Control signal scope=%s with no room_id; no-op", scope)
            return []
        case room_id, _ if room_id in executions:
            return [executions[room_id]]
        case _:
            logger.info(
                "Control signal (mode=%s) for unknown room %s; no-op",
                payload.mode,
                payload.room_id,
            )
            return []


async def apply_control(
    execution: Execution, mode: ControlMode, on_control: ControlCallback | None
) -> None:
    """Dispatch one control mode to one execution, degrading gracefully.

    Custom ``Execution`` implementations that omit a control method are
    skipped with a log, as ``request_resync`` degrades.

    INTERRUPT/STOP also notify ``on_control`` (the adapter's own
    ``on_interrupt``): the execution's own method reaches only the task still
    running the handler, not work an adapter returned from and kept running
    detached (e.g. a turn parked on a human decision). A STOP the execution
    already observed and reported for its current turn is not reported twice.
    """
    match mode:
        case ControlMode.INTERRUPT:
            _call_if_supported(execution, "interrupt")
            await _notify_adapter(execution, mode, on_control)
        case ControlMode.STOP:
            _call_if_supported(execution, "stop_room")
            if not getattr(execution, "adapter_stop_notified", False):
                await _notify_adapter(execution, mode, on_control)
        case ControlMode.PLAY:
            if (resume := _supported(execution, "resume_room")) is not None:
                await resume()
        case _:
            logger.warning("Unknown control mode %r; ignoring", mode)


def _supported(execution: Execution, name: str) -> Callable[[], Any] | None:
    method = getattr(execution, name, None)
    if method is None:
        logger.debug(
            "Execution for room %s has no %s(); skipping",
            getattr(execution, "room_id", "?"),
            name,
        )
    return method


def _call_if_supported(execution: Execution, name: str) -> None:
    if (method := _supported(execution, name)) is not None:
        method()


async def _notify_adapter(
    execution: Execution, mode: ControlMode, on_control: ControlCallback | None
) -> None:
    room_id = getattr(execution, "room_id", None)
    if on_control is not None and room_id is not None:
        await on_control(room_id, mode)
