"""Ownership of asynchronous effects from one execution turn."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from band.client.streaming import ControlMode
from band.core.turn_lifecycle import CYCLE_CANCEL_GRACE_SECONDS, TurnTask


@dataclass(eq=False)
class TurnScope:
    control_revision: int
    stop_observed: bool = False
    # A heartbeat begun after PLAY can observe STOP in the original turn.
    stop_revision: int | None = None
    observer_task: asyncio.Task[None] | None = None
    handler: TurnTask | None = None
    admitted: bool = False
    settled: bool = False
    kind: ControlMode | None = None
    deadline: float | None = None
    cooperative: bool = False
    shutdown: bool = False
    terminated: asyncio.Event = field(default_factory=asyncio.Event)
    workers: set[TurnTask] = field(default_factory=set)

    @property
    def aborted(self) -> bool:
        # Detached workers can be stopped after the room has moved to another scope.
        return self.deadline is not None or any(
            owner.deadline is not None for owner in self.workers
        )

    def bind(self, owner: TurnTask) -> None:
        self.workers.add(owner)
        if self.deadline is not None:
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
            self.deadline = (
                asyncio.get_running_loop().time() + CYCLE_CANCEL_GRACE_SECONDS
            )
            self.cooperative = cooperative
        for owner in self.workers:
            owner.terminate(deadline=self.deadline, cooperative=self.cooperative)
        if self.handler is not None:
            self.handler.terminate(deadline=self.deadline, cooperative=self.cooperative)
        self.terminated.set()
