"""The standalone door's agent tools manage a room's members and mentions over the real REST client."""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest
from band_rest import ListAgentPeersResponse

from band.runtime.tools.agent import ParticipantAddStatus
from tests.mcp.standalonedoor.kit import call, read_as, refusal, standalone_door
from tests.mcp.standalonedoor.peer import COLLEAGUE, OWNER, Identity, Platform


def delivered_to(who: Identity) -> dict[str, Any]:
    return {
        "id": mock.ANY,
        "recipients": [{"id": who.id, "handle": who.handle, "name": who.name}],
        "success": True,
    }


def mentioning(who: Identity, content: str, message_id: str) -> dict[str, Any]:
    return {
        "id": message_id,
        "content": content,
        "mentions": [{"id": who.id, "handle": who.handle}],
    }


@pytest.mark.usefixtures("platform")
class TestAgentDoor:
    async def test_lookup_peers_lists_only_peers_outside_the_room(self) -> None:
        async with standalone_door() as door:
            room_id = await call(door, "band_create_chatroom")
            await call(
                door,
                "band_add_participant",
                chat_id=room_id,
                identifier=f"@{COLLEAGUE.handle}",
            )

            peers = await call(door, "band_lookup_peers", chat_id=room_id)

        assert peers == read_as(
            ListAgentPeersResponse,
            {
                "data": [OWNER.as_peer()],
                "metadata": {
                    "page": 1,
                    "page_size": 50,
                    "total_count": 1,
                    "total_pages": 1,
                },
            },
        )

    async def test_mentioning_a_peer_outside_the_room_succeeds_once_they_are_added(
        self, platform: Platform
    ) -> None:
        async with standalone_door() as door:
            room_id = await call(door, "band_create_chatroom")
            rejected = await refusal(
                door,
                "band_send_message",
                chat_id=room_id,
                content="too early",
                mentions=[OWNER.id],
            )

            added = await call(
                door, "band_add_participant", chat_id=room_id, identifier=OWNER.id
            )
            sent = await call(
                door,
                "band_send_message",
                chat_id=room_id,
                content="welcome",
                mentions=[OWNER.id],
            )

        assert f"Unknown participant '{OWNER.id}'" in rejected
        assert added == {
            "id": OWNER.id,
            "name": OWNER.name,
            "role": "member",
            "status": ParticipantAddStatus.ADDED,
        }
        assert sent == delivered_to(OWNER)
        assert platform.rooms[room_id].messages == [
            mentioning(OWNER, "welcome", sent["id"])
        ]

    async def test_mentioning_a_member_who_joined_elsewhere_refreshes_the_room(
        self, platform: Platform
    ) -> None:
        async with standalone_door() as door:
            room_id = await call(door, "band_create_chatroom")
            rejected = await refusal(
                door,
                "band_send_message",
                chat_id=room_id,
                content="too early",
                mentions=[f"@{COLLEAGUE.handle}"],
            )

            platform.join(room_id, COLLEAGUE)
            sent = await call(
                door,
                "band_send_message",
                chat_id=room_id,
                content="review please",
                mentions=[f"@{COLLEAGUE.handle}"],
            )

        assert f"Unknown participant '{COLLEAGUE.handle}'" in rejected
        assert sent == delivered_to(COLLEAGUE)
        assert platform.rooms[room_id].messages == [
            mentioning(COLLEAGUE, "review please", sent["id"])
        ]
