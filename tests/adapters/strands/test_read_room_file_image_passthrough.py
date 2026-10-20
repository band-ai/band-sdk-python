from __future__ import annotations

import pytest

pytest.importorskip("strands", reason="strands extra not installed")


from band.adapters.strands import (
    _result_text,
    _tool_result,
)


class TestReadRoomFileImagePassthrough:
    def test_image_result_becomes_image_content_block(self):
        tool_use = {"toolUseId": "t1", "name": "band_read_room_file", "input": {}}
        value = {
            "content": [{"type": "image", "data": "ZmFrZQ==", "mimeType": "image/png"}]
        }

        result = _tool_result(tool_use, value=value, ok=True)

        assert result["content"] == [
            {"image": {"format": "png", "source": {"bytes": b"fake"}}}
        ]

    def test_non_image_result_stays_text(self):
        tool_use = {"toolUseId": "t1", "name": "band_read_room_file", "input": {}}
        value = {"name": "notes.txt", "content_type": "text/plain"}

        result = _tool_result(tool_use, value=value, ok=True)

        assert result["content"] == [
            {"text": '{"name": "notes.txt", "content_type": "text/plain"}'}
        ]

    def test_image_shaped_value_on_error_stays_text(self):
        """An error path (ok=False) must never be treated as an image result,
        even if the error value happens to look MCP-content-shaped."""
        tool_use = {"toolUseId": "t1", "name": "band_read_room_file", "input": {}}
        value = {"content": [{"type": "image", "data": "x", "mimeType": "image/png"}]}

        result = _tool_result(tool_use, value=value, ok=False)

        assert result["status"] == "error"
        assert "text" in result["content"][0]

    def test_multi_image_result_reports_one_placeholder_with_total_count(self):
        """A multi-image result must flatten to one placeholder naming the
        total count, not one placeholder line per image block."""
        tool_use = {"toolUseId": "t1", "name": "band_read_room_file", "input": {}}
        value = {
            "content": [
                {"type": "image", "data": "ZmFrZQ==", "mimeType": "image/png"},
                {"type": "image", "data": "ZmFrZQ==", "mimeType": "image/png"},
                {"type": "image", "data": "ZmFrZQ==", "mimeType": "image/png"},
            ]
        }

        result = _tool_result(tool_use, value=value, ok=True)

        assert _result_text(result) == "<3 image content block(s)>"

    def test_malformed_image_data_returns_error_result(self):
        """A decode_image_block failure (invalid base64) must degrade to an
        error result, not raise uncaught out of stream()'s async generator --
        this runs after _execute's own try/except already succeeded, so
        _tool_result needs its own boundary around the decode step."""
        tool_use = {"toolUseId": "t1", "name": "band_read_room_file", "input": {}}
        value = {"content": [{"type": "image", "data": "A", "mimeType": "image/png"}]}

        result = _tool_result(tool_use, value=value, ok=True)

        assert result["status"] == "error"
        assert "text" in result["content"][0]

    def test_result_text_keeps_images_at_their_original_position(self):
        """A combined image placeholder must appear where the first image
        block occurred among text/json blocks, not get shoved to the end."""
        result = {
            "toolUseId": "t1",
            "status": "success",
            "content": [
                {"text": "before"},
                {"image": {"format": "png", "source": {"bytes": b"a"}}},
                {"image": {"format": "png", "source": {"bytes": b"b"}}},
                {"text": "after"},
            ],
        }

        assert _result_text(result) == "before\n<2 image content block(s)>\nafter"
