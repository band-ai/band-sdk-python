"""History converter for A2A Gateway adapter."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from band.core.protocols import HistoryConverter

# Use TYPE_CHECKING to avoid circular import:
# gateway/__init__.py -> adapter.py -> this module -> gateway/types.py -> gateway/__init__.py
if TYPE_CHECKING:
    from band.integrations.a2a.gateway.types import GatewaySessionState

logger = logging.getLogger(__name__)


class GatewayHistoryConverter(HistoryConverter["GatewaySessionState"]):
    """Extracts context_id to room_id mappings from platform history.

    Unlike LLM converters that transform message content for model consumption,
    this converter extracts metadata for session state restoration. It scans
    platform history for gateway-specific metadata to rebuild the mapping
    between A2A context_ids and Band room_ids.

    The converter looks for messages with metadata containing:
    - gateway_context_id: The A2A context identifier
    - gateway_room_id: The corresponding Band room identifier

    It also tracks which peers have participated in each room by examining
    message sender information.
    """

    def convert(self, raw: list[dict[str, Any]]) -> GatewaySessionState:
        """Extract gateway session state from platform history.

        Args:
            raw: Platform history from format_history_for_llm().
                 Each dict has: role, content, sender_name, sender_type,
                 message_type, metadata, room_id, sender_id.

        Returns:
            GatewaySessionState with context_to_room and room_participants
            mappings extracted from the history.
        """
        # Runtime import to avoid circular import at module load time
        from band.integrations.a2a.gateway.types import GatewaySessionState

        context_to_room: dict[str, str] = {}
        room_participants: dict[str, set[str]] = defaultdict(set)

        # Scan history for gateway-specific metadata
        for msg in raw:
            metadata = msg.get("metadata", {})

            # Extract context_id → room_id mapping from gateway events
            if "gateway_context_id" in metadata:
                context_id = metadata["gateway_context_id"]
                room_id = metadata.get("gateway_room_id") or msg.get("room_id")
                if room_id:
                    context_to_room[context_id] = room_id
                    logger.debug(
                        "Restored context mapping: %s → %s", context_id, room_id
                    )

            # Track participants from message senders
            sender_id = msg.get("sender_id")
            sender_type = msg.get("sender_type")
            room_id = msg.get("room_id")
            if sender_type == "agent" and room_id and sender_id:
                room_participants[room_id].add(sender_id)

        state = GatewaySessionState(
            context_to_room=context_to_room,
            room_participants=dict(room_participants),
        )

        logger.debug(
            "Converted gateway history: %d contexts, %d rooms with participants",
            len(context_to_room),
            len(room_participants),
        )

        return state
