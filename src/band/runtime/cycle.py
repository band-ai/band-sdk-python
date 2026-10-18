"""Ownership of asynchronous effects from one execution turn."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from band.client.streaming import ControlMode
from band.core.turn_lifecycle import TurnTask, terminal_deadline


@dataclass(eq=False)
class TurnScope:
    control_revision: int
    # Revision at which a platform STOP was observed; a heartbeat begun after
    # PLAY can observe STOP in the original turn.
    stop_revision: int | None = None
    observer_task: asyncio.Task[None] | None = None
    handler: TurnTask | None = None
    admitted: bool = False
    activity_allowed: bool = False
    settled: bool = False
    kind: ControlMode | None = None
    deadline: float | None = None
    cooperative: bool = False
    shutdown: bool = False
    terminated: asyncio.Event = field(default_factory=asyncio.Event)
    workers: set[TurnTask] = field(default_factory=set)

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
