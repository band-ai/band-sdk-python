"""Participant tracking with change detection. Sync, unit-testable."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ParticipantTracker:
    """
    Tracks room participants and detects changes.

    Used by ExecutionContext to:
    - Track participants via WebSocket events
    - Detect when LLM needs to be notified of changes
    """

    def __init__(self, room_id: str = ""):
        self._room_id = room_id
        self._participants: list[dict[str, Any]] = []
        self._last_sent: list[dict[str, Any]] | None = None
        self._loaded = False

    @property
    def participants(self) -> list[dict[str, Any]]:
        """Get current participants (copy)."""
        return self._participants.copy()

    @property
    def is_loaded(self) -> bool:
        """Check if participants have been loaded from API."""
        return self._loaded

    def set_loaded(self, participants: list[dict[str, Any]]) -> None:
        """Set participants from API load."""
        self._participants = participants
        self._loaded = True
        logger.debug(
            "Session %s: Loaded %s participants", self._room_id, len(participants)
        )

    def add(self, participant: dict[str, Any]) -> bool:
        """
        Add participant (from WebSocket event).

        Returns:
            True if added, False if duplicate
        """
        if any(p.get("id") == participant.get("id") for p in self._participants):
            return False

        self._participants.append(
            {
                "id": participant.get("id"),
                "name": participant.get("name"),
                "type": participant.get("type"),
                "handle": participant.get("handle"),
            }
        )
        logger.debug(
            "Session %s: Added participant %s",
            self._room_id,
            participant.get("name"),
        )
        return True

    def remove(self, participant_id: str) -> bool:
        """
        Remove participant (from WebSocket event).

        Returns:
            True if removed, False if not found
        """
        before = len(self._participants)
        self._participants = [
            p for p in self._participants if p.get("id") != participant_id
        ]
        removed = len(self._participants) < before
        if removed:
            logger.debug(
                "Session %s: Removed participant %s", self._room_id, participant_id
            )
        return removed

    def changed(self) -> bool:
        """Check if participants changed since last mark_sent()."""
        if self._last_sent is None:
            return True  # First time, always send

        last_ids = {p.get("id") for p in self._last_sent}
        current_ids = {p.get("id") for p in self._participants}
        return last_ids != current_ids

    def mark_sent(self) -> None:
        """Mark current state as sent to LLM."""
        self._last_sent = self._participants.copy()
        logger.debug("Session %s: Participants sent to LLM", self._room_id)
