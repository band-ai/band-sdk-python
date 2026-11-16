"""The standalone door's agent tools manage a room's members and mentions over the real REST client."""

from __future__ import annotations

from typing import Any

import pytest
from band_rest import ListAgentPeersResponse

from band.runtime.tools.agent import ParticipantAddStatus
from tests.mcp.standalonedoor.kit import call, read_as, refusal, standalone_door
from tests.mcp.standalonedoor.peer import (
    COLLEAGUE,
    MEMBER_ROLE,
    OWNER,
    Identity,
    Platform,
    message_id,
)

# The door's only successful send in each test is the room's first message.
FIRST_MESSAGE_ID = message_id(0)
PEER_PAGE_SIZE = 10


def unknown_participant(identifier: str) -> str:
    """The SDK's refusal for a mention it cannot resolve in the room."""
    return f"Unknown participant '{identifier}'"


def delivered_to(who: Identity) -> dict[str, Any]:
    return {
        "id": FIRST_MESSAGE_ID,
        "recipients": [{"id": who.id, "handle": who.handle, "name": who.name}],
        "success": True,
    }


def mentioning(who: Identity, content: str) -> dict[str, Any]:
    return {
        "id": FIRST_MESSAGE_ID,
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

            peers = await call(
                door, "band_lookup_peers", chat_id=room_id, page_size=PEER_PAGE_SIZE
            )

        assert peers == read_as(
            ListAgentPeersResponse,
            {
                "data": [OWNER.as_peer()],
                "metadata": {
                    "page": 1,
                    "page_size": PEER_PAGE_SIZE,
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

        assert unknown_participant(OWNER.id) in rejected
        assert added == {
            "id": OWNER.id,
            "name": OWNER.name,
            "role": MEMBER_ROLE,
            "status": ParticipantAddStatus.ADDED,
        }
        assert sent == delivered_to(OWNER)
        assert platform.rooms[room_id].messages == [mentioning(OWNER, "welcome")]

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

        assert unknown_participant(COLLEAGUE.handle) in rejected
        assert sent == delivered_to(COLLEAGUE)
        assert platform.rooms[room_id].messages == [
            mentioning(COLLEAGUE, "review please")
        ]
