"""The standalone door's human tools read the owner's profile and rooms over the real REST client."""

from __future__ import annotations

import pytest
from band_rest import (
    CreateMyChatRoomResponse,
    GetMyChatRoomResponse,
    GetMyProfileResponse,
    ListMyChatsResponse,
)

from tests.mcp.standalonedoor.kit import call, read_as, standalone_door
from tests.mcp.standalonedoor.peer import OWNER, OWNER_EMAIL, room_record


@pytest.mark.usefixtures("platform")
class TestHumanDoor:
    async def test_profile_reports_the_signed_in_owner(self) -> None:
        async with standalone_door() as door:
            profile = await call(door, "band_get_my_profile")

        assert profile == read_as(
            GetMyProfileResponse,
            {
                "data": {
                    "id": OWNER.id,
                    "handle": OWNER.handle,
                    "email": OWNER_EMAIL,
                    "first_name": "Olive",
                    "last_name": "Owner",
                    "role": "user",
                }
            },
        )

    async def test_created_room_is_listed_and_fetched_by_id(self) -> None:
        async with standalone_door() as door:
            created = await call(door, "band_create_my_chat_room")
            room_id = created["data"]["id"]
            chats = await call(door, "band_list_my_chats")
            fetched = await call(door, "band_get_my_chat_room", chat_id=room_id)

        assert created == read_as(
            CreateMyChatRoomResponse, {"data": room_record(room_id)}
        )
        assert chats == read_as(
            ListMyChatsResponse,
            {
                "data": [room_record(room_id)],
                "metadata": {
                    "page": 1,
                    "page_size": 1,
                    "total_count": 1,
                    "total_pages": 1,
                },
            },
        )
        assert fetched == read_as(GetMyChatRoomResponse, {"data": room_record(room_id)})
