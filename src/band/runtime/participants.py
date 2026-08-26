"""Participant field-set projection for the passive roster, and shared
error logging for band_sdk_core.ParticipantRoster's failure surfaces."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from band.logging_config import core_issues, trace_context_extra

# Fields retained for the always-injected passive roster (and the WS/REST cache
# that feeds it). One definition so every cache path projects the same shape.
_PARTICIPANT_FIELDS = ("id", "name", "type", "handle", "description")


def participant_snapshot(participant: Mapping[str, Any]) -> dict[str, Any]:
    """Project a participant mapping to the passive-roster field set.

    Callers pass a plain dict — REST models are ``model_dump()``-ed at the
    call site, same as WebSocket event payloads. Used independently of
    ``ExecutionContext``'s core-backed roster by ``AgentTools``/
    ``OneShotInvoker``, which keep their own participant cache.
    """
    return {name: participant.get(name) for name in _PARTICIPANT_FIELDS}


def log_roster_error(
    logger_: logging.Logger, *, room_id: str, action: str, err: Exception
) -> None:
    """Log a ``ParticipantRoster`` failure with ``.issues``/``.trace_context``
    as distinct fields rather than flattened into ``str(err)``. Callers keep
    the previous roster state rather than propagating.
    """
    logger_.warning(
        "Failed to %s for room %s: %s",
        action,
        room_id,
        err,
        extra={"issues": core_issues(err), **trace_context_extra(err)},
    )


def apply_roster_change(
    logger_: logging.Logger, *, room_id: str, action: str, fn: Callable[[], object]
) -> None:
    """Run a single ``ParticipantRoster`` mutation, logging via
    :func:`log_roster_error` on its two new failure surfaces instead of
    raising. Every one of this migration's roster-mutation call sites keeps
    the previous roster state rather than propagating -- see
    :func:`log_roster_error` for why -- so they share this one try/except
    shape.
    """
    try:
        fn()
    except (ValueError, TypeError) as err:
        log_roster_error(logger_, room_id=room_id, action=action, err=err)
