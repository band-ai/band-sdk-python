"""The HUB_ROOM hub room is created with its task id only when one is configured."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from band.platform.link import BandLink
from band.runtime.contact_handler import ContactEventHandler
from band.runtime.contacts import ContactEventConfig, ContactEventStrategy
from tests.fakeplatform import CHATS_URL
from tests.runtime.contactwire.kit import HUB_ROOM_CREATED, HUB_ROOM_ID, sent

HUB_TASK_ID = "3b9e6c1a-7f24-4d8e-a5b0-2c4f6e8a0d13"


class TestHubRoomCreation:
    @pytest.mark.parametrize(
        ("hub_task_id", "chat"),
        [
            pytest.param(None, {}, id="no-task"),
            pytest.param(HUB_TASK_ID, {"task_id": HUB_TASK_ID}, id="task"),
        ],
    )
    async def test_creates_the_hub_room_with_the_configured_task(
        self,
        rest_link: BandLink,
        httpx_mock: HTTPXMock,
        hub_task_id: str | None,
        chat: dict[str, Any],
    ) -> None:
        httpx_mock.add_response(method="POST", url=CHATS_URL, json=HUB_ROOM_CREATED)
        handler = ContactEventHandler(
            ContactEventConfig(
                strategy=ContactEventStrategy.HUB_ROOM, hub_task_id=hub_task_id
            ),
            rest_link,
        )

        assert await handler.initialize_hub_room() == HUB_ROOM_ID
        assert [sent(r) for r in httpx_mock.get_requests()] == [
            ("POST", CHATS_URL, {"chat": chat})
        ]
