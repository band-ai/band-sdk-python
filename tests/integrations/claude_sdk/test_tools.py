"""Tests for the Claude SDK vision-passthrough fix.

``band_read_room_file``'s image branch returns an already MCP-shaped result
(``{"content": [{"type": "image", ...}]}``) so the model receives real vision
input. Before this fix, ``_make_result`` would json-dumps *any* dict --
including that image block -- into a text content block, so the model never
actually saw an image.

The passthrough decision is scoped to the one caller that needs it (the
``band_read_room_file`` branch of ``_build_builtin_sdk_tool``'s handler), not
baked into ``_make_result`` itself: ``_make_result`` also formats every custom
tool's result, and a loose structural check there would misfire on an
unrelated custom tool whose own return value happens to look MCP-content-shaped.
These tests pin that scoping at the unit level (``_make_result`` always
encodes; ``_format_success_payload`` only special-cases ``band_read_room_file``)
and through the real ``@tool``-decorated handler ``build_band_sdk_tools``
produces.
"""

from __future__ import annotations

import json

import pytest

from pydantic import BaseModel

from band.integrations.claude_sdk.tools import (
    _format_success_payload,
    _is_mcp_content_result,
    _make_result,
    build_band_sdk_tools,
)
from band.runtime.tools import TOOL_DEFINITIONS

_IMAGE_RESULT = {
    "content": [{"type": "image", "data": "YmFzZTY0", "mimeType": "image/png"}]
}
_TEXT_RESULT = {"name": "notes.txt", "content_type": "text/plain", "text": "hi"}


class TestIsMcpContentResult:
    def test_true_for_image_content_block(self) -> None:
        assert _is_mcp_content_result(_IMAGE_RESULT)

    def test_false_for_plain_dict(self) -> None:
        assert not _is_mcp_content_result(_TEXT_RESULT)

    def test_false_for_non_dict(self) -> None:
        assert not _is_mcp_content_result("just a string")

    def test_false_for_content_list_without_type_keys(self) -> None:
        assert not _is_mcp_content_result({"content": [{"no_type": 1}]})


class TestMakeResultAlwaysEncodes:
    """``_make_result`` has no per-tool identity, so it never special-cases a
    dict's shape -- including one that happens to look MCP-content-shaped,
    which is exactly what a custom tool's own return value could look like."""

    def test_plain_dict_is_json_encoded(self) -> None:
        result = _make_result(_TEXT_RESULT)

        assert result["content"][0]["type"] == "text"
        assert json.loads(result["content"][0]["text"]) == _TEXT_RESULT

    def test_mcp_shaped_dict_is_still_json_encoded(self) -> None:
        """Regression guard: an MCP-content-shaped dict from a source other
        than band_read_room_file (e.g. a custom tool) must not be passed
        through bare -- only the scoped call site in
        _build_builtin_sdk_tool's handler does that."""
        result = _make_result(_IMAGE_RESULT)

        assert result["content"][0]["type"] == "text"
        assert json.loads(result["content"][0]["text"]) == _IMAGE_RESULT


class TestFormatSuccessPayloadReadRoomFile:
    def test_image_result_bypasses_status_wrapping(self) -> None:
        payload = _format_success_payload("band_read_room_file", {}, _IMAGE_RESULT)

        assert payload == _IMAGE_RESULT
        assert "status" not in payload

    def test_text_result_still_gets_status_wrapped(self) -> None:
        payload = _format_success_payload("band_read_room_file", {}, _TEXT_RESULT)

        assert payload["status"] == "success"
        assert payload["text"] == "hi"


class StubReadRoomFileTools:
    """Minimal AgentToolsProtocol double whose read_room_file returns a fixed
    result -- only what the handler under test actually calls."""

    def __init__(self, result: object) -> None:
        self._result = result

    async def read_room_file(self, file_id: str) -> object:
        del file_id
        return self._result


@pytest.mark.asyncio
async def test_band_read_room_file_handler_hands_back_a_real_image_block() -> None:
    """End-to-end through the real @tool-decorated handler: an image result
    from AgentTools.read_room_file must reach the SDK exactly as an MCP image
    content block, not json-dumped into a text block."""
    tools = StubReadRoomFileTools(_IMAGE_RESULT)
    sdk_tools = build_band_sdk_tools(
        tool_definitions=[TOOL_DEFINITIONS["band_read_room_file"]],
        get_tools=lambda _room_id: tools,
        include_room_id=False,
    )
    handler = next(t for t in sdk_tools if t.name == "band_read_room_file").handler

    result = await handler({"file_id": "file-1"})

    assert result == _IMAGE_RESULT


@pytest.mark.asyncio
async def test_band_read_room_file_handler_still_json_encodes_text_result() -> None:
    """Non-image results keep the existing status-wrapped JSON-text shape."""
    tools = StubReadRoomFileTools(_TEXT_RESULT)
    sdk_tools = build_band_sdk_tools(
        tool_definitions=[TOOL_DEFINITIONS["band_read_room_file"]],
        get_tools=lambda _room_id: tools,
        include_room_id=False,
    )
    handler = next(t for t in sdk_tools if t.name == "band_read_room_file").handler

    result = await handler({"file_id": "file-1"})

    assert result["content"][0]["type"] == "text"
    payload = json.loads(result["content"][0]["text"])
    assert payload["status"] == "success"
    assert payload["text"] == "hi"


class ReportInput(BaseModel):
    """A custom tool whose own data model happens to look MCP-content-shaped."""


async def _report_handler(_input: ReportInput) -> dict[str, object]:
    return _IMAGE_RESULT


@pytest.mark.asyncio
async def test_custom_tool_result_that_looks_mcp_shaped_is_still_json_encoded() -> None:
    """Regression guard for the _make_result scoping fix: a custom tool's own
    return value can coincidentally match the MCP-content shape
    (``{"content": [{"type": ..., ...}]}``) without being band_read_room_file's
    image block. It must still reach the SDK as a json-encoded text block, not
    be passed through bare as if it were real vision content."""
    sdk_tools = build_band_sdk_tools(
        tool_definitions=[],
        get_tools=lambda _room_id: None,
        additional_tools=[(ReportInput, _report_handler)],
        include_room_id=False,
    )
    handler = next(t for t in sdk_tools if t.name == "report").handler

    result = await handler({})

    assert result["content"][0]["type"] == "text"
    assert json.loads(result["content"][0]["text"]) == _IMAGE_RESULT
