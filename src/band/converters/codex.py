"""Codex history converter."""

from __future__ import annotations

import logging
from typing import Any

from band.converters._utils import optional_str, parse_iso_datetime
from band.core.protocols import HistoryConverter
from band.integrations.codex.types import CodexSessionState

logger = logging.getLogger(__name__)


class CodexHistoryConverter(HistoryConverter["CodexSessionState"]):
    """
    Extract the latest Codex session metadata from platform task events.

    This is intentionally narrow: unlike LLM prompt converters, we only need
    room->thread mapping metadata to resume Codex threads after reconnect/restart.
    """

    def set_agent_name(self, name: str) -> None:
        """No-op: Codex converter does not use agent name."""

    def convert(self, raw: list[dict[str, Any]]) -> CodexSessionState:
        """Return most recent Codex session state found in history."""
        logger.debug("CodexHistoryConverter: scanning %d messages", len(raw))

        for msg in reversed(raw):
            if msg.get("message_type") != "task":
                continue

            metadata = msg.get("metadata") or {}
            if not isinstance(metadata, dict):
                continue

            thread_id = metadata.get("codex_thread_id")
            if not thread_id:
                continue

            created_at = parse_iso_datetime(metadata.get("codex_created_at"))
            return CodexSessionState(
                thread_id=str(thread_id),
                room_id=optional_str(metadata.get("codex_room_id")),
                created_at=created_at,
            )

        return CodexSessionState()
