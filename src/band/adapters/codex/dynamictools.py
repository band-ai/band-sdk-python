"""Responses to the dynamic tool calls Codex asks the adapter to run."""

from __future__ import annotations

from typing import Any

from band.adapters.codex.rpc import text_item


def image_content_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    """An image tool result as Codex app-server ``inputImage`` content items.

    Inlined as a data: URI -- the protocol's ``imageUrl`` is a bare string with
    no documented inline-vs-http distinction.
    """
    return [
        {
            "type": "inputImage",
            "imageUrl": f"data:{block['mimeType']};base64,{block['data']}",
        }
        for block in result["content"]
    ]


UNOWNED_TOOL_CALL_TEXT = (
    "No Band turn started this Codex turn, so Band tools are unavailable to it."
)


def tool_response(items: list[dict[str, Any]], *, success: bool) -> dict[str, Any]:
    """A dynamic tool call's response."""
    return {"contentItems": items, "success": success}


def text_tool_result(text: str, *, success: bool) -> dict[str, Any]:
    """A dynamic tool call's response carrying one text item."""
    return tool_response([text_item(text)], success=success)


def tools_by_name(tools: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(tool.get("name")): tool for tool in tools}
