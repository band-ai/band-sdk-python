"""Room file tools — gated behind ``Capability.FILES``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from parlant.core.tools import ToolContext, ToolResult

from band.integrations.parlant.bandtool import band_tool, or_none
from band.integrations.parlant.mentions import (
    SEND_MESSAGE_MENTIONS_NOTE,
    SEND_MESSAGE_MENTIONS_PARAM_NOTE,
    missing_mentions_error,
    split_mentions,
)
from band.integrations.parlant.sessiontools import require_session_tools
from band.runtime.tools import (
    BandTool,
    is_mcp_content_result,
    platform_args_schema,
    serialize_tool_result,
    validate_tool_arguments,
)


@band_tool("listing room files")
async def band_list_room_files(
    context: ToolContext,
    cursor: str = "",
) -> ToolResult:
    tools = require_session_tools(context)
    data = serialize_tool_result(await tools.list_room_files(or_none(cursor)))
    files = data.get("data") or []
    if not files:
        return ToolResult(data="No files found in this room")

    lines = ["Files in this room:"]
    lines.extend(f"- {_file_summary(file)} id={file.get('id', '')}" for file in files)
    if next_cursor := data.get("next_cursor"):
        lines.append(
            f"More files available; call again with cursor='{next_cursor}' "
            "to see the rest."
        )
    return ToolResult(data="\n".join(lines))


@band_tool("reading room file")
async def band_read_room_file(
    context: ToolContext,
    file_id: str,
) -> ToolResult:
    tools = require_session_tools(context)
    args = validate_tool_arguments(
        BandTool.READ_ROOM_FILE,
        platform_args_schema(BandTool.READ_ROOM_FILE),
        {"file_id": file_id},
    )
    result = await tools.read_room_file(args["file_id"])
    match result:
        case {"text": str() as text}:
            note = result.get("description")
            return ToolResult(data=f"{text}\n\n({note})" if note else text)
        case _ if is_mcp_content_result(result):
            # A Parlant ToolResult has no multimodal channel, so the
            # image is described rather than passed through as vision.
            mime_type = result["content"][0].get("mimeType", "image")
            return ToolResult(
                data=(
                    f"This file is a {mime_type} image; image content cannot "
                    "be shown inline by this tool."
                )
            )

    summary = _file_summary(result)
    description = result.get("description", "")
    return ToolResult(data=f"{summary}. {description}" if description else summary)


@band_tool(
    "sending room file",
    extra_doc=SEND_MESSAGE_MENTIONS_NOTE,
    param_notes={"mentions": SEND_MESSAGE_MENTIONS_PARAM_NOTE},
    mention_hints=True,
)
async def band_send_room_file(
    context: ToolContext,
    content: str,
    filename: str,
    mentions: str,
    caption: str = "",
) -> ToolResult:
    tools = require_session_tools(context)
    recipients = split_mentions(mentions)
    if not recipients:
        return ToolResult(data=missing_mentions_error(tools))

    result = await tools.send_room_file(content, filename, caption, recipients)
    attachment = result.get("attachment") or {}
    return ToolResult(
        data=f"Uploaded '{attachment.get('name', filename)}' "
        f"(id={attachment.get('id', '')}) and shared with "
        f"{', '.join(recipients)}"
    )


def _file_summary(file: Mapping[str, Any]) -> str:
    """A room file's name, type and size, as both file tools render it."""
    return (
        f"{file.get('name', 'Unknown')} "
        f"({file.get('content_type', 'unknown')}, {file.get('bytes', 0)} bytes)"
    )


TOOLS = (
    band_list_room_files,
    band_read_room_file,
    band_send_room_file,
)
