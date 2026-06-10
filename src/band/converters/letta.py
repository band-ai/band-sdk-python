"""Letta history converter."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from band.converters._utils import optional_str, parse_iso_datetime
from band.core.protocols import HistoryConverter

logger = logging.getLogger(__name__)


@dataclass
class LettaSessionState:
    """Session state extracted from platform history for Letta agent rehydration."""

    agent_id: str | None = None
    conversation_id: str | None = None
    room_id: str | None = None
    created_at: datetime | None = None

    def has_agent(self) -> bool:
        """Return True when a persisted Letta agent_id is available."""
        return bool(self.agent_id)


class LettaHistoryConverter(HistoryConverter["LettaSessionState"]):
    """
    Extract the latest Letta session metadata from platform task events.

    Like the Codex converter, this is intentionally narrow: we only need
    room->agent_id mapping metadata to resume Letta agents after reconnect/restart.
    """

    def set_agent_name(self, name: str) -> None:
        """No-op: Letta converter does not use agent name."""

    def convert(self, raw: list[dict[str, Any]]) -> LettaSessionState:
        """Return most recent Letta session state found in history."""
        logger.debug("LettaHistoryConverter: scanning %d messages", len(raw))

        for msg in reversed(raw):
            if msg.get("message_type") != "task":
                continue

            metadata = msg.get("metadata") or {}
            if not isinstance(metadata, dict):
                continue

            agent_id = metadata.get("letta_agent_id")
            if not agent_id:
                continue

            created_at = parse_iso_datetime(metadata.get("letta_created_at"))
            return LettaSessionState(
                agent_id=str(agent_id),
                conversation_id=optional_str(metadata.get("letta_conversation_id")),
                room_id=optional_str(metadata.get("letta_room_id")),
                created_at=created_at,
            )

        return LettaSessionState()
