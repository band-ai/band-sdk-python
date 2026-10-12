"""Ownership of asynchronous effects from one execution turn."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(eq=False)
class TurnScope:
    control_revision: int
    stop_observed: bool = False
    # A heartbeat begun after PLAY can observe STOP in the original turn.
    stop_revision: int | None = None
    observer_task: asyncio.Task[None] | None = None
