"""Parlant tool definitions that wrap Band AgentTools.

Tools are created at server startup via :func:`create_parlant_tools` and use the
session-keyed registry to reach the current room's tools during execution. The
tools mirror the LangGraph/Claude adapters and are grouped by category:

- chat/room tools (``chat``): send_message, send_event, add/remove_participant,
  lookup_peers, get_participants, create_chatroom
- contact tools (``contacts``): list/add/remove contacts, list/respond requests
- memory tools (``memory``): list/store/get/supersede/archive memories

Contact tools are included unless the CONTACTS capability is absent; memory tools
are included only when the MEMORY capability is present.
"""

from __future__ import annotations

from band.integrations.parlant.tools.factory import create_parlant_tools
from band.integrations.parlant.tools.registry import (
    get_current_tools,
    get_session_tools,
    mark_message_sent,
    set_current_tools,
    set_session_tools,
    was_message_sent,
)

__all__ = [
    "create_parlant_tools",
    "get_current_tools",
    "get_session_tools",
    "mark_message_sent",
    "set_current_tools",
    "set_session_tools",
    "was_message_sent",
]
