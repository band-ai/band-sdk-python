"""The residency vocabulary shared by rooms, providers and observers."""

from __future__ import annotations

from enum import StrEnum

from band.core.adapterconfig import BaseAdapterConfig

UNKNOWN_ACTIVITY_REASON = "Provider has not established authoritative quiescence"
BUSY_ACTIVITY_REASON = "Provider refused idle release"


class ProviderActivity(StrEnum):
    ACTIVE = "active"
    QUIESCENT = "quiescent"
    UNKNOWN = "unknown"


class Residency(StrEnum):
    RESIDENT = "resident"
    SUSPENDING = "suspending"
    SUSPENDED = "suspended"
    ACQUIRING = "acquiring"
    CLEANUP_PENDING = "cleanup_pending"
    DISPOSING = "disposing"
    CLOSED = "closed"


class ReleaseCause(StrEnum):
    """Why a physical release runs; later members supersede earlier ones."""

    IDLE = "idle"
    RECOVERY = "recovery"
    FINAL = "final"


class ReleaseOutcome(StrEnum):
    """A release callback's successful result; raising means incomplete cleanup.

    ``BUSY`` is valid only for ``IDLE``, and only when nothing was torn down.
    """

    RELEASED = "released"
    BUSY = "busy"


_PRECEDENCE = tuple(ReleaseCause)


def stronger_cause(
    current: ReleaseCause | None, requested: ReleaseCause
) -> ReleaseCause:
    """The cause that wins when ``requested`` arrives while ``current`` pends."""
    if current is None:
        return requested
    return max(current, requested, key=_PRECEDENCE.index)


class RetentionSnapshot(BaseAdapterConfig):
    state: Residency
    generation: int
    leases: int
    accepted: int
    provider_activity: ProviderActivity
    reason: str
    deadline: float | None
    pending_release: ReleaseCause | None
    pending_cleanup: bool
    error: str | None
