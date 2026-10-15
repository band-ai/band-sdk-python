from __future__ import annotations

from typing import Any, ClassVar

from band.runtime.tools import is_mcp_content_result


class TestIsMcpContentResult:
    """``is_mcp_content_result`` recognizes ``read_room_file``'s image-branch
    shape so a consumer that supports real MCP content (claude_sdk, the MCP
    engine) can pass it through instead of json.dumps-ing it into text."""

    _IMAGE_RESULT: ClassVar[dict[str, Any]] = {
        "content": [{"type": "image", "data": "YmFzZTY0", "mimeType": "image/png"}]
    }
    _TEXT_RESULT: ClassVar[dict[str, Any]] = {
        "name": "notes.txt",
        "content_type": "text/plain",
        "text": "hi",
    }

    def test_true_for_image_content_block(self) -> None:
        assert is_mcp_content_result(self._IMAGE_RESULT)

    def test_false_for_plain_dict(self) -> None:
        assert not is_mcp_content_result(self._TEXT_RESULT)

    def test_false_for_non_dict(self) -> None:
        assert not is_mcp_content_result("just a string")

    def test_false_for_content_list_without_type_keys(self) -> None:
        assert not is_mcp_content_result({"content": [{"no_type": 1}]})

    def test_false_for_empty_content_list(self) -> None:
        """An empty list is vacuously all()-true; every consumer indexes
        content[0] right after this check, so an empty list must not pass."""
        assert not is_mcp_content_result({"content": []})
