"""User-side operation driver for live E2E tests.

Acts as the test *user* (the driver, not the agent under test) to set up and
probe scenarios: create and delete rooms, send messages, manage participants.
SDK-backed operations call the Human API client. Room deletion has no SDK
method yet, so it uses a direct REST call (see ``delete_room``).
"""

from __future__ import annotations

from datetime import datetime

import httpx
from band_rest import (
    AsyncRestClient,
    ChatMessage,
    ChatMessageRequest,
    ChatMessageRequestMentionsItem,
    CreateMyChatRoomRequestChat,
    ListMyPeersRequestType,
    ParticipantRequest,
    Peer,
)

from band.core.types import MessageType


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

    async def whoami(self) -> str:
        """Return the driving user's own id, via the Human profile endpoint.

        The subject id for memories *about the user*. Used when a scenario must
        assert an agent resolved *the user's* identity itself (e.g. inferred
        subject-scoped memory) rather than being handed the id in the prompt.
        """
        response = await self._client.human_api_profile.get_my_profile()
        return response.data.id

    async def lookup_peers(
        self,
        *,
        not_in_room: str | None = None,
        peer_type: ListMyPeersRequestType | None = None,
        page: int = 1,
        limit: int = 100,
    ) -> list[Peer]:
        """List one page of peers the user can interact with — the invitable roster.

        The driver-side mirror of the agent's own ``band_lookup_peers``: it asks
        the Human API which entities (users and agents) the test user could bring
        into a room. Pass ``not_in_room=<room_id>`` to exclude peers already in
        that room, so the result is exactly the set still invitable there;
        ``peer_type`` (``"User"``/``"Agent"``) narrows by kind. Returns the ``Peer``
        models so callers match on ``.name``/``.id``/``.handle``/``.type``.

        Returns a single page (``limit`` items from ``page``); a caller that must
        see the whole roster in a populated workspace pages until a short page.

        ``not_in_room``/``peer_type`` are query params: left ``None`` they are
        omitted (not sent as ``null``), matching how the SDK itself calls this
        endpoint, so no conditional kwargs bag is needed.
        """
        response = await self._client.human_api_peers.list_my_peers(
            not_in_chat=not_in_room, type=peer_type, page=page, page_size=limit
        )
        return list(response.data or [])

    async def list_messages(
        self,
        room_id: str,
        *,
        message_type: MessageType | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[ChatMessage]:
        """List a room's messages/events, optionally filtered by type and time.

        Returns every item the platform records for the room (text plus event
        types like ``tool_call``/``tool_result``), so it doubles as the read
        path for an agent's tool calls. Newest-first from the API; reversed
        here to chronological (oldest-first) so callers read a turn in order.
        ``None`` ``message_type`` returns all types; ``since`` (a server
        timestamp) keeps only items after it. Both are query params: left
        ``None`` they are omitted (not sent as ``null``), so they pass directly.
        """
        response = await self._client.human_api_messages.list_my_chat_messages(
            room_id, message_type=message_type, since=since, limit=limit
        )
        return list(reversed(response.data or []))

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
