"""Participant field-set projection for the passive roster, and shared
error logging for band_sdk_core.ParticipantRoster's failure surfaces."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from band.logging_config import trace_context_extra

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
    """Log a ``band_sdk_core.ParticipantRoster`` failure with its structured fields.

    ``ParticipantRoster.add``/``set_all`` are real, new failure surfaces at
    call sites that never raised before this migration (a plain ``TypeError``
    for a malformed field, or a ``ValueError`` with ``.issues``/
    ``.trace_context`` for a duplicate id in ``set_all``). Every caller keeps
    the previous roster/participant state rather than propagating; this
    helper is only the shared log line, so ``.issues``/``.trace_context``
    reach the log as distinct fields instead of being flattened into
    ``str(err)`` (harmless ``None``s for a plain REST exception, which has
    neither).
    """
    logger_.warning(
        "Failed to %s for room %s: %s",
        action,
        room_id,
        err,
        extra={"issues": getattr(err, "issues", None), **trace_context_extra(err)},
    )
