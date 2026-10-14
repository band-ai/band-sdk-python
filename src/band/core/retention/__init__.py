"""Adapter-owned room residency, split by concern across `config`, `states`,
`clock`, `cleanup`, `room` and `owner`; native handles and continuation stay
with adapters. This file re-exports the public surface.
"""

from __future__ import annotations

from band.core.retention.cleanup import report_owned, task_failure
from band.core.retention.clock import MonotonicClock, RetentionClock
from band.core.retention.config import RoomRetentionConfig, require_disabled_retention
from band.core.retention.owner import RoomRetention
from band.core.retention.room import RetainedRoom
from band.core.retention.states import (
    BUSY_ACTIVITY_REASON,
    UNKNOWN_ACTIVITY_REASON,
    ProviderActivity,
    ReleaseCause,
    ReleaseOutcome,
    Residency,
    RetentionSnapshot,
)

__all__ = [
    "BUSY_ACTIVITY_REASON",
    "UNKNOWN_ACTIVITY_REASON",
    "MonotonicClock",
    "ProviderActivity",
    "ReleaseCause",
    "ReleaseOutcome",
    "Residency",
    "RetainedRoom",
    "RetentionClock",
    "RetentionSnapshot",
    "RoomRetention",
    "RoomRetentionConfig",
    "report_owned",
    "require_disabled_retention",
    "task_failure",
]
