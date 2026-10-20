from __future__ import annotations

import json
from typing import Any

from agno.tools.function import ToolResult

from band.adapters.agno import (
    _bind_room_tools,
    _make_band_entrypoint,
)
from band.testing import FakeAgentTools


class TestBandEntrypointBinding:
    async def test_routes_to_execute_tool_call_inside_context(self, tools):
        entry = _make_band_entrypoint("band_lookup_peers")

        with _bind_room_tools(tools):
            result = await entry(page=1)

        assert tools.tool_calls == [
            {"tool_name": "band_lookup_peers", "arguments": {"page": 1}}
        ]
        assert json.loads(result) == {"status": "ok"}

    async def test_passes_string_results_through_unchanged(self):
        class _StrTools(FakeAgentTools):
            async def execute_tool_call(self, tool_name: str, arguments: dict) -> Any:
                return "raw-string"

        entry = _make_band_entrypoint("band_lookup_peers")
        with _bind_room_tools(_StrTools()):
            assert await entry() == "raw-string"

    async def test_errors_outside_any_bound_context(self, tools):
        entry = _make_band_entrypoint("band_lookup_peers")

        # Bind then exit; the ContextVar must reset so later calls have no tools.
        with _bind_room_tools(tools):
            pass
        result = await entry(page=1)

        assert "no active Band context" in result
        assert tools.tool_calls == []

    async def test_read_room_file_image_result_passes_through_as_agno_image(self):
        class _ImageTools(FakeAgentTools):
            async def execute_tool_call(self, tool_name: str, arguments: dict) -> Any:
                return {
                    "content": [
                        {"type": "image", "data": "ZmFrZQ==", "mimeType": "image/png"}
                    ]
                }

        entry = _make_band_entrypoint("band_read_room_file")
        with _bind_room_tools(_ImageTools()):
            result = await entry(file_id="f1")

        assert isinstance(result, ToolResult)
        assert result.images is not None
        assert len(result.images) == 1
        assert result.images[0].content == b"fake"
        assert result.images[0].mime_type == "image/png"

    async def test_read_room_file_non_image_result_stays_json(self):
        class _TextTools(FakeAgentTools):
            async def execute_tool_call(self, tool_name: str, arguments: dict) -> Any:
                return {"name": "notes.txt", "content_type": "text/plain"}

        entry = _make_band_entrypoint("band_read_room_file")
        with _bind_room_tools(_TextTools()):
            result = await entry(file_id="f1")

        assert result == '{"name": "notes.txt", "content_type": "text/plain"}'

    async def test_read_room_file_malformed_image_data_returns_error_string(self):
        """A decode_image_block failure (invalid base64) must degrade to the
        adapter's usual error string, not raise uncaught out of the tool
        entrypoint Agno invokes directly."""

        class _MalformedImageTools(FakeAgentTools):
            async def execute_tool_call(self, tool_name: str, arguments: dict) -> Any:
                return {
                    "content": [{"type": "image", "data": "A", "mimeType": "image/png"}]
                }

        entry = _make_band_entrypoint("band_read_room_file")
        with _bind_room_tools(_MalformedImageTools()):
            result = await entry(file_id="f1")

        assert isinstance(result, str)
        assert result.startswith("Error reading room file:")
