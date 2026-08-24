"""Tests for the Claude SDK vision-passthrough fix.

``band_read_room_file``'s image branch returns an already MCP-shaped result
(``{"content": [{"type": "image", ...}]}``) so the model receives real vision
input. Before this fix, ``_make_result`` would json-dumps *any* dict --
including that image block -- into a text content block, so the model never
actually saw an image. These tests pin the passthrough at both the unit level
(``_make_result``/``_format_success_payload``) and through the real
``@tool``-decorated handler ``build_band_sdk_tools`` produces.
"""

from __future__ import annotations

import json

import pytest

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


class TestMakeResultPassthrough:
    def test_image_result_passes_through_untouched(self) -> None:
        assert _make_result(_IMAGE_RESULT) is _IMAGE_RESULT

    def test_plain_dict_is_still_json_encoded(self) -> None:
        result = _make_result(_TEXT_RESULT)

        assert result["content"][0]["type"] == "text"
        assert json.loads(result["content"][0]["text"]) == _TEXT_RESULT


class TestFormatSuccessPayloadReadRoomFile:
    def test_image_result_bypasses_status_wrapping(self) -> None:
        payload = _format_success_payload("band_read_room_file", {}, _IMAGE_RESULT)

        assert payload == _IMAGE_RESULT
        assert "status" not in payload

    def test_text_result_still_gets_status_wrapped(self) -> None:
        payload = _format_success_payload("band_read_room_file", {}, _TEXT_RESULT)

        assert payload["status"] == "success"
        assert payload["text"] == "hi"


class _StubReadRoomFileTools:
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
    tools = _StubReadRoomFileTools(_IMAGE_RESULT)
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
    tools = _StubReadRoomFileTools(_TEXT_RESULT)
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
