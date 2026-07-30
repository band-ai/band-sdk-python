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
class Tick:
    """When a monitor loop last called, and the quantum it waited.

    The quantum travels with the tick because it is the caller's to pick per
    call, so a limit read off the install default would call a healthy
    long-quantum loop stopped after a single wait.
    """

    at: datetime
    quantum: float

    def stale(self, now: datetime) -> bool:
        return (now - self.at).total_seconds() > self.quantum + STALE_GRACE_S


class RoomSession:
    """The mutable per-room facts the monitor workflow decides by."""

    def __init__(self, now: Callable[[], datetime]) -> None:
        self._now = now
        self._modes: dict[str, AttentionMode] = {}
        self._model_ticks: dict[str, Tick] = {}
        self._view_ticks: dict[str, Tick] = {}
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
        self._model_ticks[chat_id] = Tick(self._now(), quantum)

    def note_view_tick(self, chat_id: str, *, quantum: float) -> None:
        """Record that a live widget is showing this room.

        Called for the display loop's ticks, and once when a mounting tool
        grants the view — the widget's first own tick trails the grant by a
        few seconds, and that gap must not read as a missing view.
        """
        self._view_ticks[chat_id] = Tick(self._now(), quantum)

    def view_missing(self, chat_id: str) -> bool:
        """Whether no live widget shows this room any more.

        The mirror of :meth:`monitoring`: there the widget's steady ticks
        expose the agent's stopped loop, here the agent's tick exposes a dead
        widget — one that never mounted in this conversation, or that a
        Desktop restart killed without a word. The agent can repair this
        (remount via the show tool) only when told, so the monitor summary
        carries it.
        """
        tick = self._view_ticks.get(chat_id)
        return tick is None or tick.stale(self._now())

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
        return MonitoringStatus(
            idle_seconds=(self._now() - tick.at).total_seconds(),
            stale=tick.stale(self._now()),
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
