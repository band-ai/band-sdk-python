"""Render stored mention markers as readable participant handles."""

from __future__ import annotations

from typing import Any

from tests.integrations.desktop_app.conftest import message


class TestMentionRendering:
    async def test_stored_markers_are_shown_as_handles(self, room: Any) -> None:
        """Raw `@[[id]]` teaches the agent to write it back, which Band cannot resolve."""
        greeting = message("m-1", "2026-01-01T00:00:01Z")
        greeting["content"] = "hi @[[jerry-1]] and @[[human-1]]"

        joined = await room([greeting]).join()

        assert joined["messages"][0]["content"] == (
            "hi @alexander.zaikman/jery and @alexander.zaikman"
        )
