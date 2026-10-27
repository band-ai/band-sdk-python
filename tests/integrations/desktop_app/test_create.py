"""Open a newly created room and report creation failures."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from band.integrations.desktop_app.tools import RoomTool
from tests.integrations.desktop_app.conftest import message


class TestCreatingTheRoom:
    async def test_create_opens_the_created_room(self, room: Any) -> None:
        live = room([message("m-1", "2026-01-01T00:00:01Z")])

        created = await live.create("task-1")

        assert live.tools.created_task_ids == ["task-1"]
        assert created["chat_id"] == "created-room"
        assert live.tools.calls[0]["chat_id"] == "created-room"
        assert created["messages"][0]["id"] == "m-1"
        assert created["summary_text"].startswith("Created Band room created-room.")
        assert "Joined live Band room created-room" in created["summary_text"]

    async def test_create_failure_does_not_try_to_open_a_room(self, room: Any) -> None:
        live = room([])
        live.tools.create_room = AsyncMock(side_effect=RuntimeError("create failed"))

        result = await live.call(RoomTool.CREATE)

        assert result.isError
        assert live.tools.calls == []
