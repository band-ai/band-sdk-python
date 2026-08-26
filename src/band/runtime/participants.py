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


def log_roster_call(
    logger_: logging.Logger, *, call: Callable[[Any], Any], arg: Any, room_id: str
) -> None:
    """Call a single ``ParticipantRoster`` mutator, logging and suppressing a
    validation failure. ``action`` in the log line is ``call``'s own
    qualified name rather than a hand-written paraphrase, so it can't drift
    from what actually ran.
    """
    try:
        call(arg)
    except (ValueError, TypeError) as err:
        log_roster_error(logger_, room_id=room_id, action=call.__qualname__, err=err)
