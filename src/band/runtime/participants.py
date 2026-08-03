"""Participant field-set projection and merge for the passive roster."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Fields retained for the always-injected passive roster (and the WS/REST cache
# that feeds it). One definition so every cache path projects the same shape.
_PARTICIPANT_FIELDS = ("id", "name", "type", "handle", "description")


def participant_snapshot(participant: Mapping[str, Any]) -> dict[str, Any]:
    """Project a participant mapping to the passive-roster field set.

    Callers pass a plain dict — REST models are ``model_dump()``-ed at the
    call site, same as WebSocket event payloads.
    """
    return {name: participant.get(name) for name in _PARTICIPANT_FIELDS}


def merge_participant(
    existing: Mapping[str, Any], snapshot: Mapping[str, Any]
) -> dict[str, Any]:
    """Merge a fresh snapshot over an existing record, field by field.

    Participant data arrives from sources of unequal fidelity (Peer lookup,
    participant list, WebSocket events, integration hooks), so a source that
    does not know a field (``None``) must never erase one learned elsewhere.
    """
    return {
        name: (
            snapshot.get(name) if snapshot.get(name) is not None else existing.get(name)
        )
        for name in _PARTICIPANT_FIELDS
    }
