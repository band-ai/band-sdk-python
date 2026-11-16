"""create_chatroom sends the chat body the platform accepts and returns the new room."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from band.runtime.tools import AgentTools
from tests.httpwire import request_json
from tests.identifiers import UUID_ID
from tests.runtime.helpers import rest_client_over

ROOM_ID = "room-123"
STAMP = "2026-01-01T00:00:00Z"
CREATE_CHAT = ("POST", "/api/v1/agent/chats")


class TestAgentToolsCreateChatroom:
    @pytest.mark.parametrize(
        ("task_id", "chat"),
        [
            pytest.param(None, {}, id="no-task"),
            pytest.param(UUID_ID, {"task_id": UUID_ID}, id="task"),
        ],
    )
    async def test_creates_the_room_with_the_given_task(
        self, task_id: str | None, chat: dict[str, Any]
    ) -> None:
        sent: list[tuple[str, str, Any]] = []

        def platform(request: httpx.Request) -> httpx.Response:
            sent.append((request.method, request.url.path, request_json(request)))
            if (request.method, request.url.path) != CREATE_CHAT:
                return httpx.Response(404)
            return httpx.Response(
                200,
                json={
                    "data": {"id": ROOM_ID, "inserted_at": STAMP, "updated_at": STAMP}
                },
            )

        async with rest_client_over(platform) as rest:
            room_id = await AgentTools("room-456", rest).create_chatroom(
                task_id=task_id
            )

        assert room_id == ROOM_ID
        assert sent == [(*CREATE_CHAT, {"chat": chat})]
