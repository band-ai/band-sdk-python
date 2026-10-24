from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from band.runtime.messages import PlatformMessage
from band.runtime.sessionconfig import SessionConfig
from tests.conftest import mirror_settlement_results

_FAST = SessionConfig(working_keep_alive_seconds=4.0)


def _working_values(link: MagicMock) -> list[bool]:
    """Sequence of `working` booleans passed to link.report_activity."""
    return [
        c.args[1] if len(c.args) > 1 else c.kwargs["working"]
        for c in link.report_activity.call_args_list
    ]


def _backlog_message(
    room_id: str = "room-123", msg_id: str = "msg-b1"
) -> PlatformMessage:
    return PlatformMessage(
        id=msg_id,
        room_id=room_id,
        content="hi",
        sender_id="user-1",
        sender_type="User",
        sender_name="User One",
        message_type="text",
        metadata={},
        created_at=datetime.now(UTC),
    )


def _independent_link() -> MagicMock:
    link = MagicMock()
    link.agent_id = "agent-123"
    link.rest = MagicMock()
    p = MagicMock(id="user-1", name="User One", type="User")
    link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
        return_value=MagicMock(data=[p])
    )
    link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
        return_value=MagicMock(data=[])
    )
    link.mark_processed = AsyncMock(return_value=True)
    link.mark_failed = AsyncMock(return_value=True)
    link.report_activity = AsyncMock(return_value=True)
    return mirror_settlement_results(link)
