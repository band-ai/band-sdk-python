"""Participant field-set projection for the passive roster, and shared
error logging for band_sdk_core.ParticipantRoster's failure surfaces."""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
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


@contextmanager
def log_roster_errors(
    logger_: logging.Logger, *, room_id: str, action: str
) -> Iterator[None]:
    """Log and suppress roster validation failures within the block."""
    try:
        yield
    except (ValueError, TypeError) as err:
        log_roster_error(logger_, room_id=room_id, action=action, err=err)
