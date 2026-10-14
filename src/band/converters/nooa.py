"""NOOA history converter."""

from __future__ import annotations

import logging
from typing import Any

from band.converters.helpers import build_replay_messages
from band.core.protocols import HistoryConverter

logger = logging.getLogger(__name__)

NooaTranscript = list[str]


class NooaHistoryConverter(HistoryConverter[NooaTranscript]):
    """Render platform history as ``[sender]: content`` transcript lines.

    A NOOA agent keeps its own event log for the life of the instance, so the
    transcript only seeds an agent created after the room already had history.
    """

    def __init__(self, agent_name: str = "") -> None:
        self._agent_name = agent_name

    def set_agent_name(self, name: str) -> None:
        """Record the agent's name; transcript lines already name every sender."""
        self._agent_name = name

    def convert(self, raw: list[dict[str, Any]]) -> NooaTranscript:
        transcript = build_replay_messages(raw)
        logger.debug(
            "Converted %d platform event(s) into %d NOOA transcript line(s)",
            len(raw),
            len(transcript),
        )
        return transcript
