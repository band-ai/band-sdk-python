"""The room chat tools every Parlant agent gets, regardless of capabilities."""

from __future__ import annotations

from typing import get_args

from parlant.core.tools import ToolContext, ToolResult

from band.core.types import EventMessageType
from band.integrations.parlant.bandtool import band_tool, invalid_choice, or_none
from band.integrations.parlant.mentions import (
    SEND_MESSAGE_MENTIONS_NOTE,
    SEND_MESSAGE_MENTIONS_PARAM_NOTE,
    missing_mentions_error,
    split_mentions,
)
from band.integrations.parlant.sessiontools import require_session_tools
from band.runtime.tools import ParticipantAddStatus, serialize_tool_result

EVENT_MESSAGE_TYPES: tuple[str, ...] = get_args(EventMessageType)

# The master model describes lookup_peers' raw return shape (a 'data'/'metadata'
# dict) for adapters that pass it through unchanged. This Parlant tool formats
# that result into a plain-text summary instead, so the master claim would be
# wrong here without this correction.
LOOKUP_PEERS_RETURN_NOTE = (
    "\n\nThis tool returns a formatted text summary of matching agents, not "
    "the 'data'/'metadata' dict described above."
)


@band_tool(
    "sending message",
    extra_doc=SEND_MESSAGE_MENTIONS_NOTE,
    param_notes={"mentions": SEND_MESSAGE_MENTIONS_PARAM_NOTE},
    mention_hints=True,
)
async def band_send_message(
    context: ToolContext,
    content: str,
    mentions: str,
) -> ToolResult:
    tools = require_session_tools(context)
    recipients = split_mentions(mentions)
    if not recipients:
        return ToolResult(data=missing_mentions_error(tools))

    await tools.send_message(content, recipients)
    return ToolResult(data=f"Message sent to {', '.join(recipients)}")


@band_tool("sending event")
async def band_send_event(
    context: ToolContext,
    content: str,
    message_type: str,
) -> ToolResult:
    tools = require_session_tools(context)
    if message_type not in EVENT_MESSAGE_TYPES:
        return invalid_choice("message_type", message_type, EVENT_MESSAGE_TYPES)

    await tools.send_event(content, message_type, None)
    return ToolResult(data=f"Event ({message_type}) sent successfully")


@band_tool("ending the turn without a reply")
async def band_no_reply(
    context: ToolContext,
    reason: str = "",
) -> ToolResult:
    tools = require_session_tools(context)
    await tools.no_reply(or_none(reason))
    return ToolResult(data="No reply sent; this turn is complete")


@band_tool("adding participant '{identifier}'")
async def band_add_participant(
    context: ToolContext,
    identifier: str,
) -> ToolResult:
    tools = require_session_tools(context)
    result = await tools.add_participant(identifier)
    if result.get("status") == ParticipantAddStatus.ALREADY_IN_ROOM:
        return ToolResult(
            data=f"'{identifier}' is already in the room - no action needed"
        )
    return ToolResult(data=f"Successfully added '{identifier}' to the room")


@band_tool("removing participant '{identifier}'")
async def band_remove_participant(
    context: ToolContext,
    identifier: str,
) -> ToolResult:
    tools = require_session_tools(context)
    await tools.remove_participant(identifier)
    return ToolResult(data=f"Successfully removed '{identifier}' from the room")


@band_tool("looking up peers", extra_doc=LOOKUP_PEERS_RETURN_NOTE)
async def band_lookup_peers(
    context: ToolContext,
) -> ToolResult:
    tools = require_session_tools(context)
    # Pagination is rarely needed for agent lookups, so it isn't exposed.
    data = serialize_tool_result(await tools.lookup_peers(page=1, page_size=50))
    peers = data.get("data") or []
    if not peers:
        return ToolResult(data="No available agents found")

    metadata = data.get("metadata") or {}
    lines = [
        (
            f"Available agents (page {metadata.get('page', 1)} of "
            f"{metadata.get('total_pages', 1)}):"
        )
    ]
    lines.extend(
        f"- {peer.get('name', 'Unknown')} ({peer.get('type', 'Agent')}): "
        f"{peer.get('description') or 'No description'}"
        for peer in peers
    )
    return ToolResult(data="\n".join(lines))


@band_tool("getting participants")
async def band_get_participants(
    context: ToolContext,
) -> ToolResult:
    tools = require_session_tools(context)
    result = await tools.get_participants()
    if not isinstance(result, list):
        return ToolResult(data=str(result))

    participants = serialize_tool_result(result)
    if not participants:
        return ToolResult(data="No participants in the room")
    lines = ["Current participants:"]
    lines.extend(
        f"- {participant.get('name', 'Unknown')} ({participant.get('type', 'Unknown')})"
        for participant in participants
    )
    return ToolResult(data="\n".join(lines))


@band_tool("creating chatroom")
async def band_create_chatroom(
    context: ToolContext,
    task_id: str = "",
) -> ToolResult:
    tools = require_session_tools(context)
    room_id = await tools.create_chatroom(or_none(task_id))
    return ToolResult(data=f"Created new chat room: {room_id}")


TOOLS = (
    band_send_message,
    band_send_event,
    band_no_reply,
    band_add_participant,
    band_remove_participant,
    band_lookup_peers,
    band_get_participants,
    band_create_chatroom,
)
