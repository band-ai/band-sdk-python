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
    metadata_to_dict,
)
from band.integrations.base import check_and_format_participants
from band.platform.event import MessageEvent, PlatformEvent
from band.runtime.execution import ExecutionContext
from band.runtime.formatters import (
    format_history_for_llm,
    messages_before,
    replace_uuid_mentions,
)
from band.runtime.tools import AgentTools

logger = logging.getLogger(__name__)


class DefaultPreprocessor(Preprocessor):
    """
    Default message preprocessor.

    Handles:
    - Self-message filtering
    - Event to PlatformMessage conversion (using tagged union pattern matching)
    - Session bootstrap detection + history loading (respects enable_context_hydration)
    - Participant change detection
    - AgentTools creation
    """

    async def process(
        self,
        ctx: ExecutionContext,
        event: PlatformEvent,
        agent_id: str,
    ) -> AgentInput | None:
        """Process platform event into AgentInput."""
        msg = self.message(ctx, event, agent_id)
        if msg is None:
            return None

        is_bootstrap = not ctx.is_llm_initialized

        # Load history on session bootstrap (if hydration enabled)
        raw_history: list[dict[str, Any]] = []
        if is_bootstrap and ctx.config.enable_context_hydration:
            raw_history = await self._load_history(ctx, msg)

        # Check participants
        participants = ctx.participants
        participants_msg = check_and_format_participants(ctx, mark_sent=False)

        # Get pending system messages (contact broadcasts)
        system_messages = ctx.peek_pending_system_messages()
        contacts_msg = "\n".join(system_messages) if system_messages else None

        def commit_context() -> None:
            if is_bootstrap:
                ctx.mark_llm_initialized()
            if participants_msg is not None:
                ctx.mark_participants_sent(participants)
            ctx.commit_system_messages(system_messages)

        # Create tools
        tools = AgentTools.from_context(ctx)

        return AgentInput(
            msg=msg,
            tools=tools,
            history=HistoryProvider(raw=raw_history),
            participants_msg=participants_msg,
            contacts_msg=contacts_msg,
            is_session_bootstrap=is_bootstrap,
            room_id=msg.room_id,
            commit_context=commit_context,
        )

    @staticmethod
    def message(
        ctx: ExecutionContext, event: PlatformEvent, agent_id: str
    ) -> PlatformMessage | None:
        """Project a delivery without consuming model context."""
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

        # Look up sender name from participants list
        sender_name = DefaultPreprocessor._lookup_sender_name(ctx, msg_data.sender_id)

        # Convert to PlatformMessage (typed attribute access, no dict lookups).
        # Translate @[[uuid]] mention tokens to @handle like history formatting
        # does: the platform normalizes mentions to uuid form, and a raw uuid in
        # the turn misleads the LLM (e.g. into using it as a memory subject_id).
        return PlatformMessage(
            id=msg_data.id,
            room_id=room_id,
            content=replace_uuid_mentions(msg_data.content, ctx.participants),
            sender_id=msg_data.sender_id,
            sender_type=msg_data.sender_type,
            sender_name=sender_name,
            message_type=msg_data.message_type,
            metadata=metadata_to_dict(msg_data.metadata, exclude_none=True),
            created_at=datetime.fromisoformat(msg_data.inserted_at),
        )

    @staticmethod
    def _lookup_sender_name(ctx: ExecutionContext, sender_id: str) -> str | None:
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
            # Strictly before the trigger: later entries are pending turns of
            # their own; replaying them exposes future requests and duplicates
            # them when their own turn arrives.
            history = format_history_for_llm(
                messages_before(context.messages, msg.id),
                exclude_id=msg.id,
                participants=ctx.participants,
            )
            logger.info(
                "Room %s: Got %s messages",
                ctx.room_id,
                len(history) if history else 0,
            )
            return history or []
        except Exception as e:  # noqa: BLE001 -- best-effort event emission must not crash the turn/link
            logger.warning("Room %s: Failed to load history: %s", ctx.room_id, e)
            return []
