"""Contact management tools — gated behind ``Capability.CONTACTS``."""

from __future__ import annotations

from typing import Any

from parlant.core.tools import ToolContext, ToolResult

from band.core.types import ContactRequestAction, ContactRequestStatus
from band.integrations.parlant.bandtool import band_tool, invalid_choice, or_none
from band.integrations.parlant.sessiontools import require_session_tools
from band.runtime.tools import serialize_tool_result, tool_result_text

CONTACT_ACTIONS: tuple[str, ...] = tuple(ContactRequestAction)


@band_tool("listing contacts")
async def band_list_contacts(
    context: ToolContext,
    page: int = 1,
    page_size: int = 50,
) -> ToolResult:
    tools = require_session_tools(context)
    return ToolResult(data=tool_result_text(await tools.list_contacts(page, page_size)))


@band_tool("adding contact")
async def band_add_contact(
    context: ToolContext,
    handle: str,
    message: str = "",
) -> ToolResult:
    tools = require_session_tools(context)
    status = _reported_status(
        await tools.add_contact(handle, or_none(message)),
        default=ContactRequestStatus.PENDING,
    )
    return ToolResult(data=f"Contact request to {handle}: {status}")


@band_tool("removing contact")
async def band_remove_contact(
    context: ToolContext,
    handle: str = "",
    contact_id: str = "",
) -> ToolResult:
    tools = require_session_tools(context)
    if not handle and not contact_id:
        return ToolResult(data="Error: Either handle or contact_id must be provided")

    await tools.remove_contact(or_none(handle), or_none(contact_id))
    return ToolResult(data=f"Contact '{handle or contact_id}' removed successfully")


@band_tool("listing contact requests")
async def band_list_contact_requests(
    context: ToolContext,
    page: int = 1,
    page_size: int = 50,
    sent_status: str = ContactRequestStatus.PENDING,
) -> ToolResult:
    tools = require_session_tools(context)
    return ToolResult(
        data=tool_result_text(
            await tools.list_contact_requests(page, page_size, sent_status)
        )
    )


@band_tool("responding to contact request")
async def band_respond_contact_request(
    context: ToolContext,
    action: str,
    handle: str = "",
    request_id: str = "",
) -> ToolResult:
    tools = require_session_tools(context)
    if not handle and not request_id:
        return ToolResult(data="Error: Either handle or request_id must be provided")
    if action not in CONTACT_ACTIONS:
        return invalid_choice("action", action, CONTACT_ACTIONS)

    status = _reported_status(
        await tools.respond_contact_request(
            action, or_none(handle), or_none(request_id)
        ),
        default=action,
    )
    return ToolResult(data=f"Contact request {action}d: {status}")


def _reported_status(result: Any, *, default: str) -> str:
    """The status a contact reply reports, or *default* when it has none."""
    match serialize_tool_result(result):
        case {"status": str(status)}:
            return status
        case _:
            return default


TOOLS = (
    band_list_contacts,
    band_add_contact,
    band_remove_contact,
    band_list_contact_requests,
    band_respond_contact_request,
)
