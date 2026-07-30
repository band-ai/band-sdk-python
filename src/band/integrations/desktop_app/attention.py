"""Per-room session state: attention mode, monitor health, view ownership.

Everything here is bookkeeping about *this conversation's relationship to a
room* — nothing about the room's content, which is the transcript service's
job. One instance lives per server process, keyed by room id throughout.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from band.integrations.desktop_app.room import MonitoringStatus
from band.integrations.desktop_app.tools import AttentionMode

logger = logging.getLogger(__name__)

# How long after its wait should have returned the agent may take to call
# again before its loop is reported stopped rather than busy. An iteration
# costs one whole quantum plus whatever the agent does with what it got, and
# that work does not scale with the quantum — so this is added to the agent's
# own wait, not multiplied by it.
STALE_GRACE_S = 30


@dataclass
class ModelTick:
    """When the agent last monitored, and the quantum it chose to wait.

    The quantum is the model's to pick per call, so a limit read off the
    install default would call a healthy long-quantum loop stopped after a
    single wait.
    """

    at: datetime
    quantum: float


class RoomSession:
    """The mutable per-room facts the monitor workflow decides by."""

    def __init__(self, now: Callable[[], datetime]) -> None:
        self._now = now
        self._modes: dict[str, AttentionMode] = {}
        self._model_ticks: dict[str, ModelTick] = {}
        self._reported_stale: set[str] = set()
        self._view_owner: dict[str, str] = {}
        self._view_instances: dict[str, set[str]] = {}

    def mode(self, chat_id: str) -> AttentionMode:
        """Whose attention this room gets first."""
        return self._modes.get(chat_id, AttentionMode.USER_FIRST)

    def set_mode(self, chat_id: str, mode: AttentionMode) -> None:
        if mode is not self.mode(chat_id):
            logger.info("attention chat=%s mode=%s", chat_id, mode)
        self._modes[chat_id] = mode

    def note_model_tick(self, chat_id: str, *, quantum: float) -> None:
        """Record that the agent's own monitor loop is still running."""
        self._model_ticks[chat_id] = ModelTick(self._now(), quantum)

    def monitoring(self, chat_id: str) -> MonitoringStatus:
        """How long since the agent last monitored, and whether that gap means
        its loop stopped.

        Unknown until the agent has monitored once: before that the join
        summary is already telling it to start. In user-first attention, not
        monitoring is the intended state, so the whole concept is disarmed
        rather than nagging.
        """
        if self.mode(chat_id) is not AttentionMode.ROOM_FIRST:
            return MonitoringStatus()
        tick = self._model_ticks.get(chat_id)
        if tick is None:
            return MonitoringStatus()
        idle = (self._now() - tick.at).total_seconds()
        return MonitoringStatus(
            idle_seconds=idle,
            stale=idle > tick.quantum + STALE_GRACE_S,
        )

    def claim_stale_report(self, chat_id: str, monitoring: MonitoringStatus) -> bool:
        """Whether this is the first stale reading since the loop last ran.

        The view keeps ticking whatever the agent does, so a stopped loop is
        seen again every few seconds; reporting each one would bury the log it
        is meant to be found in.
        """
        if not monitoring.stale:
            self._reported_stale.discard(chat_id)
            return False
        if chat_id in self._reported_stale:
            return False
        self._reported_stale.add(chat_id)
        return True

    def view_is_current(self, chat_id: str, instance: str | None) -> bool:
        """Whether this view instance still owns the room's display.

        Ownership goes to the most recently *first-seen* instance: a fresh
        mount announces an id the room has never ticked with, takes over, and
        every previously seen id — told it is superseded — collapses instead
        of living on as a duplicate widget. An old instance ticking again is
        already known, so it can never steal ownership back.
        """
        if not instance:
            return True
        seen = self._view_instances.setdefault(chat_id, set())
        if instance not in seen:
            seen.add(instance)
            self._view_owner[chat_id] = instance
            logger.info("view instance chat=%s owner=%s", chat_id, instance)
        return self._view_owner[chat_id] == instance
