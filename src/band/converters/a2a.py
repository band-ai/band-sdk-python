"""A2A history converter."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from band.core.protocols import HistoryConverter

if TYPE_CHECKING:
    from band.integrations.a2a.types import A2ASessionState

logger = logging.getLogger(__name__)


class A2AHistoryConverter(HistoryConverter["A2ASessionState"]):
    """
    Extracts A2A session state from platform history.

    Unlike other converters that transform history for LLM consumption,
    this converter extracts A2A-specific metadata (context_id, task_id,
    task_state) from task events to restore session state.

    Output: A2ASessionState with context_id, task_id, and task_state
    """

    def convert(self, raw: list[dict[str, Any]]) -> A2ASessionState:
        """Extract most recent A2A session state from history.

        Scans history in reverse to find the latest task event with
        A2A metadata (a2a_context_id, a2a_task_id, a2a_task_state).

        Args:
            raw: Raw platform history (list of message dicts)

        Returns:
            A2ASessionState with extracted context_id, task_id, and task_state
        """
        # Import at runtime to avoid circular import
        from band.integrations.a2a.types import A2ASessionState

        context_id: str | None = None
        task_id: str | None = None
        task_state: str | None = None

        logger.debug("A2AHistoryConverter: scanning %d messages", len(raw))

        # Scan history in reverse to find latest A2A task event
        for msg in reversed(raw):
            msg_type = msg.get("message_type")
            # Look for task events with A2A metadata
            if msg_type == "task":
                metadata = msg.get("metadata", {})
                logger.debug(
                    "Found task event: content=%r, metadata=%r",
                    msg.get("content", "")[:50],
                    metadata,
                )
                if "a2a_context_id" in metadata:
                    context_id = metadata.get("a2a_context_id")
                    task_id = metadata.get("a2a_task_id")
                    task_state = metadata.get("a2a_task_state")
                    break  # Found latest A2A task event

        logger.debug(
            "A2AHistoryConverter result: context_id=%s, task_id=%s, task_state=%s",
            context_id,
            task_id,
            task_state,
        )

        return A2ASessionState(
            context_id=context_id,
            task_id=task_id,
            task_state=task_state,
        )
