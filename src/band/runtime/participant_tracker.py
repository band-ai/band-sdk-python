"""Participant field-set projection for the passive roster."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Fields retained for the always-injected passive roster (and the WS/REST cache
# that feeds it). Keep this list in one place so load_participants, tool cache
# refresh, and tracker/add paths cannot drift.
_PARTICIPANT_FIELDS = ("id", "name", "type", "handle", "description")


def participant_snapshot(participant: Mapping[str, Any]) -> dict[str, Any]:
    """Project a participant mapping to the passive-roster field set.

    Callers pass a plain dict — REST models are ``model_dump()``-ed at the
    call site, same as WebSocket event payloads.
    """
    return {name: participant.get(name) for name in _PARTICIPANT_FIELDS}
