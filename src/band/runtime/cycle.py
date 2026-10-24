"""Ownership of asynchronous effects from one execution turn."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum

from band.client.streaming import ControlMode, DeliveryStatus
from band.core.turn_lifecycle import TurnTask, terminal_deadline
from band.platform.event import MessageEvent
from band.runtime.activity import TurnActivity


class AttemptPhase(Enum):
    PREPARING = "preparing"
    ADMITTED = "admitted"
    ABANDONED = "abandoned"
    SETTLED = "settled"


@dataclass(eq=False)
class TurnScope:
    """One room-owned attempt: its originals share one handler and one outcome."""

    control_revision: int
    # The ordered originals this attempt consumes; fixed before admission.
    originals: tuple[MessageEvent, ...] = ()
    # The terminal write every original owes, chosen once at the synchronous
    # settlement point. ``None`` leaves the originals recoverable.
    intent: DeliveryStatus | None = None
    # False when a control probe declined the original, leaving it unconsumed.
    consumed: bool = True
    # Revision at which a platform STOP was observed; a heartbeat begun after
    # PLAY can observe STOP in the original turn.
    stop_revision: int | None = None
    observer_task: asyncio.Task[None] | None = None
    handler: TurnTask | None = None
    phase: AttemptPhase = AttemptPhase.PREPARING
    # Only a message handler's turn shows the room's working state; housekeeping
    # and control-probe turns, and the workers they bind, never report.
    reports_activity: bool = False
    kind: ControlMode | None = None
    deadline: float | None = None
    cooperative: bool = False
    shutdown: bool = False
    terminated: asyncio.Event = field(default_factory=asyncio.Event)
    workers: set[TurnTask] = field(default_factory=set)
    activity: TurnActivity = field(default_factory=TurnActivity)

    @property
    def admitted(self) -> bool:
        return self.phase in {AttemptPhase.ADMITTED, AttemptPhase.SETTLED}

    @property
    def settled(self) -> bool:
        return self.phase in {AttemptPhase.ABANDONED, AttemptPhase.SETTLED}

    def admit(self) -> None:
        if self.phase is not AttemptPhase.PREPARING:
            raise RuntimeError("only a preparing attempt can be admitted")
        self.phase = AttemptPhase.ADMITTED

    def settle(self, intent: DeliveryStatus | None = None) -> bool:
        """Freeze the first disposition before cleanup or transport can suspend."""
        if self.settled:
            return False
        if intent is not None and self.phase is not AttemptPhase.ADMITTED:
            raise RuntimeError("an unadmitted attempt cannot consume its inputs")
        self.intent = intent
        self.phase = AttemptPhase.SETTLED if self.admitted else AttemptPhase.ABANDONED
        return True

    @property
    def stop_observed(self) -> bool:
        return self.stop_revision is not None

    @property
    def terminating(self) -> bool:
        return self.deadline is not None

    @property
    def aborted(self) -> bool:
        # Detached workers can be stopped after the room has moved to another scope.
        return self.terminating or any(
            owner.deadline is not None for owner in self.workers
        )

    def bind(self, owner: TurnTask) -> None:
        self.workers.add(owner)
        if self.terminating:
            owner.terminate(deadline=self.deadline, cooperative=self.cooperative)

    def terminate(
        self,
        kind: ControlMode | None = None,
        *,
        cooperative: bool = False,
        observed: bool = False,
        shutdown: bool = False,
    ) -> None:
        if (
            kind is not None
            and not self.settled
            and (not observed or self.kind is None)
        ):
            self.kind = kind
        self.shutdown |= shutdown
        if self.deadline is None:
            self.deadline = terminal_deadline()
            self.cooperative = cooperative
        for owner in self.workers:
            owner.terminate(deadline=self.deadline, cooperative=self.cooperative)
        if self.handler is not None:
            self.handler.terminate(deadline=self.deadline, cooperative=self.cooperative)
        self.terminated.set()
