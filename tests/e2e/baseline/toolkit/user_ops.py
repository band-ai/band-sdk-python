"""User-side operation driver for live E2E tests.

Acts as the test *user* (the driver, not the agent under test) to set up and
probe scenarios: create and delete rooms, send messages, manage participants.
SDK-backed operations call the Human API client. Room deletion has no SDK
method yet, so it uses a direct REST call (see ``delete_room``).
"""

from __future__ import annotations

import httpx
from band_rest import (
    AsyncRestClient,
    ChatMessageRequest,
    ChatMessageRequestMentionsItem,
    CreateMyChatRoomRequestChat,
    ParticipantRequest,
)


class UserOps:
    """Drive platform actions as the test user via the Human API."""

    def __init__(self, client: AsyncRestClient) -> None:
        self._client = client

    async def create_room(self, *, title: str | None = None) -> str:
        """Create a room as the user; return its id."""
        response = await self._client.human_api_chats.create_my_chat_room(
            chat=CreateMyChatRoomRequestChat(title=title)
        )
        return response.data.id

    async def send_message(
        self, room_id: str, content: str, *, mention_id: str, mention_name: str
    ) -> str:
        """Send a message mentioning the target agent; return the message id.

        The @mention satisfies the platform's mention requirement and triggers
        the agent (which ignores its own messages, so the user must send).
        """
        response = await self._client.human_api_messages.send_my_chat_message(
            room_id,
            message=ChatMessageRequest(
                content=f"@{mention_name} {content}",
                mentions=[
                    ChatMessageRequestMentionsItem(id=mention_id, name=mention_name)
                ],
            ),
        )
        return response.data.id

    async def add_participant(
        self, room_id: str, participant_id: str, *, role: str = "member"
    ) -> None:
        await self._client.human_api_participants.add_my_chat_participant(
            room_id,
            participant=ParticipantRequest(participant_id=participant_id, role=role),
        )

    async def remove_participant(self, room_id: str, participant_id: str) -> None:
        await self._client.human_api_participants.remove_my_chat_participant(
            room_id, participant_id
        )

    async def list_participant_ids(self, room_id: str) -> list[str]:
        response = await self._client.human_api_participants.list_my_chat_participants(
            room_id
        )
        return [participant.id for participant in (response.data or [])]

    async def delete_room(self, room_id: str) -> None:
        """Soft-delete a room.

        TODO: switch to the Human API SDK method once it exposes a delete-chat
        operation. Until then call the REST endpoint directly, reusing the SDK
        client's base URL and auth headers so credentials stay in one place.
        """
        wrapper = self._client._client_wrapper
        url = f"{wrapper.get_base_url().rstrip('/')}/api/v1/me/chats/{room_id}"
        async with httpx.AsyncClient(timeout=30.0) as http:
            response = await http.delete(url, headers=wrapper.get_headers())
            response.raise_for_status()
