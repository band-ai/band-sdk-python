"""Default preprocessor - handles common preprocessing logic."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from band.core.protocols import Preprocessor
from band.core.types import (
    AgentInput,
    HistoryProvider,
    PlatformMessage,
    is_text_message_type,
)
from band.platform.event import MessageEvent, PlatformEvent
from band.runtime.execution import ExecutionContext
from band.runtime.tools import AgentTools
from band.runtime.formatters import format_history_for_llm
from band.runtime.types import SYNTHETIC_CONTACT_EVENTS_SENDER_ID
from band.integrations.base import check_and_format_participants

logger = logging.getLogger(__name__)


class DefaultPreprocessor(Preprocessor):
    """
    Default message preprocessor.

    Handles:
    - Self-message filtering
    - Mention gating (mentions are the only way to wake an agent)
    - Event to PlatformMessage conversion (using tagged union pattern matching)
    - Session bootstrap detection + history loading (respects enable_context_hydration)
    - Participant change detection
    - AgentTools creation
    """

    def __init__(self, *, require_mention: bool = True) -> None:
        """
        Args:
            require_mention: When True (default), unmentioned room text does not
                wake the agent. Set False to process every room text message
                (pre-mention-gate behavior).
        """
        self._require_mention = require_mention

    async def process(
        self,
        ctx: ExecutionContext,
        event: PlatformEvent,
        agent_id: str,
    ) -> AgentInput | None:
        """Process platform event into AgentInput."""
        # Pattern match on tagged union - only handle MessageEvent
        match event:
            case MessageEvent(room_id=room_id, payload=msg_data):
                pass  # Continue processing
            case _:
                return None  # Skip non-message events

        # msg_data is now MessageCreatedPayload (fully typed)
        if msg_data is None:
            return None

        # Validate room_id is present (narrows type from str | None to str)
        if not room_id:
            logger.error("MessageEvent has no room_id - cannot process")
            return None

        # Skip messages from self
        if msg_data.sender_type == "Agent" and msg_data.sender_id == agent_id:
            logger.debug("Room %s: Skipping own message %s", room_id, msg_data.id)
            return None

        if not self._should_wake_agent(ctx, msg_data, agent_id):
            logger.debug(
                "Room %s: Skipping message %s because it does not mention agent %s",
                room_id,
                msg_data.id,
                agent_id,
            )
            return None

        # Look up sender name from participants list
        sender_name = self._lookup_sender_name(ctx, msg_data.sender_id)

        # Convert to PlatformMessage (typed attribute access, no dict lookups)
        msg = PlatformMessage(
            id=msg_data.id,
            room_id=room_id,
            content=msg_data.content,
            sender_id=msg_data.sender_id,
            sender_type=msg_data.sender_type,
            sender_name=sender_name,
            message_type=msg_data.message_type,
            metadata=msg_data.metadata,  # Pass through as-is (Any type)
            created_at=datetime.fromisoformat(
                msg_data.inserted_at.replace("Z", "+00:00")
            ),
        )

        is_bootstrap = not ctx.is_llm_initialized

        # Load history on session bootstrap (if hydration enabled)
        raw_history: list[dict[str, Any]] = []
        if is_bootstrap:
            if ctx.config.enable_context_hydration:
                raw_history = await self._load_history(ctx, msg)
            ctx.mark_llm_initialized()

        # Check participants
        participants_msg = check_and_format_participants(ctx)

        # Get pending system messages (contact broadcasts)
        contacts_msg = self._drain_system_messages(ctx)

        # Create tools
        tools = AgentTools.from_context(ctx)

        return AgentInput(
            msg=msg,
            tools=tools,
            history=HistoryProvider(raw=raw_history),
            participants_msg=participants_msg,
            contacts_msg=contacts_msg,
            is_session_bootstrap=is_bootstrap,
            room_id=room_id,
        )

    def _should_wake_agent(
        self,
        ctx: ExecutionContext,
        msg_data: Any,
        agent_id: str,
    ) -> bool:
        """Return whether an inbound platform message should trigger this agent."""
        if not is_text_message_type(msg_data.message_type):
            return False

        if not self._require_mention:
            return True

        if msg_data.sender_id == SYNTHETIC_CONTACT_EVENTS_SENDER_ID:
            return True

        metadata = msg_data.metadata
        mentions = getattr(metadata, "mentions", None)
        if mentions is None and isinstance(metadata, dict):
            mentions = metadata.get("mentions")
        if not mentions:
            return False

        agent_handles = {
            str(participant.get(field, "")).lstrip("@")
            for participant in ctx.participants
            for field in ("handle", "username")
            if participant.get("id") == agent_id and participant.get(field)
        }
        for mention in mentions:
            mention_id = self._mention_field(mention, "id")
            if str(mention_id) == agent_id:
                return True

            for field in ("handle", "username"):
                value = self._mention_field(mention, field)
                if value and str(value).lstrip("@") in agent_handles:
                    return True

        return False

    @staticmethod
    def _mention_field(mention: Any, field: str) -> Any:
        value = getattr(mention, field, None)
        if value is None and isinstance(mention, dict):
            value = mention.get(field)
        return value

    def _drain_system_messages(self, ctx: ExecutionContext) -> str | None:
        """Drain pending system messages from context.

        Returns:
            Combined system messages as a single string, or None if no messages.
        """
        messages = ctx.get_pending_system_messages()
        if not messages:
            return None
        return "\n".join(messages)

    def _lookup_sender_name(self, ctx: ExecutionContext, sender_id: str) -> str | None:
        """Look up sender name from participants list by sender_id."""
        for participant in ctx.participants:
            if participant.get("id") == sender_id:
                return participant.get("name")
        return None

    async def _load_history(
        self,
        ctx: ExecutionContext,
        msg: PlatformMessage,
    ) -> list[dict[str, Any]]:
        """Load platform history for session bootstrap."""
        try:
            logger.info("Room %s: Loading history...", ctx.room_id)
            context = await ctx.get_context()
            history = format_history_for_llm(
                context.messages,
                exclude_id=msg.id,
                participants=ctx.participants,
            )
            logger.info(
                "Room %s: Got %s messages",
                ctx.room_id,
                len(history) if history else 0,
            )
            return history or []
        except Exception as e:
            logger.warning("Room %s: Failed to load history: %s", ctx.room_id, e)
            return []
