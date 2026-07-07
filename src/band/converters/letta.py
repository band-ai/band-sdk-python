"""Letta history converter."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from band.converters.helpers import (
    build_replay_messages,
    optional_str,
    parse_iso_datetime,
)
from band.core.protocols import HistoryConverter

logger = logging.getLogger(__name__)


@dataclass
class LettaSessionState:
    """Session state extracted from platform history for Letta agent rehydration."""

    agent_id: str | None = None
    conversation_id: str | None = None
    room_id: str | None = None
    created_at: datetime | None = None
    # The room's text history as "[sender]: content" lines. Used to seed a fresh
    # Letta agent when there is no live agent to resume (cold boot into a room
    # that already has history) — resume-by-agent_id stays the fast path.
    replay_messages: list[str] = field(default_factory=list)

    def has_agent(self) -> bool:
        """Return True when a persisted Letta agent_id is available."""
        return bool(self.agent_id)


class LettaHistoryConverter(HistoryConverter["LettaSessionState"]):
    """
    Extract Letta session state from platform history.

    Two complementary reads of the same history: the latest task-event metadata
    (``letta_agent_id``, ...) to resume the server-side Letta agent, and the
    room's text messages as replay lines so a room whose agent cannot be resumed
    is seeded from platform history instead of cold-booting into amnesia.
    """

    def set_agent_name(self, name: str) -> None:
        """No-op: Letta converter does not use agent name."""

    def convert(self, raw: list[dict[str, Any]]) -> LettaSessionState:
        """Return most recent Letta session state found in history."""
        logger.debug("LettaHistoryConverter: scanning %d messages", len(raw))
        replay_messages = build_replay_messages(raw)

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
                replay_messages=replay_messages,
            )

        return LettaSessionState(replay_messages=replay_messages)
