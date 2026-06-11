"""OpenCode history converter."""

from __future__ import annotations

import logging
from typing import Any

from band.converters._utils import optional_str, parse_iso_datetime
from band.core.protocols import HistoryConverter
from band.integrations.opencode.types import OpencodeSessionState

logger = logging.getLogger(__name__)


class OpencodeHistoryConverter(HistoryConverter["OpencodeSessionState"]):
    """Extract the latest OpenCode session metadata from task events."""

    def set_agent_name(self, name: str) -> None:
        """No-op for metadata-only converter compatibility."""

    def convert(self, raw: list[dict[str, Any]]) -> OpencodeSessionState:
        logger.debug("OpencodeHistoryConverter: scanning %d messages", len(raw))
        replay_messages = self._build_replay_messages(raw)

        for msg in reversed(raw):
            if msg.get("message_type") != "task":
                continue

            metadata = msg.get("metadata") or {}
            if not isinstance(metadata, dict):
                continue

            session_id = metadata.get("opencode_session_id")
            if not session_id:
                continue

            created_at = parse_iso_datetime(metadata.get("opencode_created_at"))
            return OpencodeSessionState(
                session_id=str(session_id),
                room_id=optional_str(metadata.get("opencode_room_id")),
                created_at=created_at,
                replay_messages=replay_messages,
            )

        return OpencodeSessionState(replay_messages=replay_messages)

    @staticmethod
    def _build_replay_messages(raw: list[dict[str, Any]]) -> list[str]:
        replay_messages: list[str] = []

        for msg in raw:
            if msg.get("message_type") != "text":
                continue

            content = optional_str(msg.get("content"))
            if not content:
                continue

            sender_name = (
                optional_str(msg.get("sender_name"))
                or optional_str(msg.get("sender_type"))
                or "Unknown"
            )
            replay_messages.append(f"[{sender_name}]: {content}")

        return replay_messages
