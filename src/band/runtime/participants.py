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
    does not know a field must never erase one learned elsewhere. Absence is
    checked by truthiness, not ``is not None`` — matching the consumer
    (``build_participants_message``, which itself gates on truthiness), since
    a source can serialize an unknown field as ``""`` rather than omitting
    the key (plausible for the participants-list endpoint, precisely the
    sparse source this merge exists to defend against).
    """
    return {
        name: snapshot.get(name) or existing.get(name) for name in _PARTICIPANT_FIELDS
    }
