"""User-side operation driver for live E2E tests.

Acts as the test *user* (the driver, not the agent under test) to set up and
probe scenarios: create and delete rooms, send messages, manage participants.
SDK-backed operations call the Human API client. Room deletion has no SDK
method yet, so it uses a direct REST call (see ``delete_room``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

import httpx
from band_rest import (
    AsyncRestClient,
    ChatMessage,
    ChatMessageRequest,
    ChatMessageRequestMentionsItem,
    CreateContactRequestRequestContactRequest,
    CreateMyChatRoomRequestChat,
    ListMyPeersRequestType,
    ParticipantRequest,
    Peer,
    UserDetails,
)
from band_rest.core.api_error import ApiError

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

    async def profile(self) -> UserDetails:
        response = await self._client.human_api_profile.get_my_profile()
        return response.data

    async def has_contact(self, user_id: str) -> bool:
        """Check the entire paginated roster for an existing relationship."""
        page = 1
        while True:
            contacts = await self._client.human_api_contacts.list_my_contacts(
                page=page, page_size=100
            )
            if any(contact.contact_id == user_id for contact in contacts.data):
                return True
            if page >= contacts.metadata.total_pages:
                return False
            page += 1

    @asynccontextmanager
    async def contact_with(self, other: UserOps) -> AsyncIterator[str]:
        """Establish a shared CI identity contact without revoking another job's access."""
        other_profile = await other.profile()
        if not await self.has_contact(other_profile.id):
            await self._establish_contact(other, other_profile)
        assert await self.has_contact(other_profile.id), (
            "contact setup did not establish the owner's relationship"
        )
        assert await other.has_contact(await self.whoami()), (
            "contact setup did not establish the second user's relationship"
        )
        yield other_profile.id

    async def _received_request_from(self, requester_id: str) -> str | None:
        """Find an incoming pending request across the entire request roster."""
        page = 1
        while True:
            received = (
                await self._client.human_api_contacts.list_received_contact_requests(
                    page=page, page_size=100
                )
            )
            request_id = next(
                (
                    request.id
                    for request in received.data
                    if request.requester_id == requester_id
                ),
                None,
            )
            if request_id is not None or page >= received.metadata.total_pages:
                return request_id
            page += 1

    async def _approve_contact_request(self, request_id: str, other_id: str) -> None:
        try:
            await self._client.human_api_contacts.approve_contact_request(request_id)
        except ApiError as error:
            if error.status_code != 409 or not await self.has_contact(other_id):
                raise

    async def _accept_pending_contact(self, other: UserOps, other_id: str) -> bool:
        owner_id = await self.whoami()
        if request_id := await other._received_request_from(owner_id):
            await other._approve_contact_request(request_id, owner_id)
            return True
        if request_id := await self._received_request_from(other_id):
            await self._approve_contact_request(request_id, other_id)
            return True
        return False

    async def _establish_contact(
        self, other: UserOps, other_profile: UserDetails
    ) -> None:
        if await self._accept_pending_contact(other, other_profile.id):
            return

        try:
            request = await self._client.human_api_contacts.create_contact_request(
                contact_request=CreateContactRequestRequestContactRequest(
                    recipient_handle=other_profile.handle
                )
            )
        except ApiError as error:
            if error.status_code != 409:
                raise
            if await self.has_contact(other_profile.id):
                return
            if await self._accept_pending_contact(other, other_profile.id):
                return
            raise
        if not await self.has_contact(other_profile.id):
            await other._approve_contact_request(request.data.id, await self.whoami())

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

    async def stop_agent(self, room_id: str) -> None:
        """Stop agents in a user-owned room through the control endpoint.

        The generated Human API has not exposed control operations yet, so this
        follows the same authenticated raw-REST boundary as :meth:`delete_room`.
        """
        await self._post_control(f"/api/v1/me/chats/{room_id}/agents/stop")

    async def play_agent(self, room_id: str) -> str:
        """Resume agents in a user-owned room through the control endpoint.

        Returns the platform's response body, so a replay that never arrives
        can be traced to the signal the platform says it sent.
        """
        response = await self._control_request(
            "POST", f"/api/v1/me/chats/{room_id}/agents/play"
        )
        return response.text

    async def interrupt_active_agent_execution(self, agent_id: str) -> None:
        """Interrupt one active execution of a user-owned agent.

        Agent-scope interrupt fans out to the agent's active rooms, so any
        execution id is a valid target. The platform must expose one once the
        controlled handler has entered its cycle.
        """
        response = await self._control_request(
            "GET", f"/api/v1/me/agents/{agent_id}/executions"
        )
        payload = response.json()
        assert isinstance(payload, dict), "execution list response was not an object"
        executions = payload.get("data")
        assert isinstance(executions, list) and executions, (
            f"no active execution available for agent {agent_id}"
        )
        execution = executions[0]
        assert isinstance(execution, dict) and isinstance(execution.get("id"), str), (
            "execution list response contained no execution id"
        )
        await self._post_control(
            f"/api/v1/me/agents/{agent_id}/executions/{execution['id']}/interrupt"
        )

    async def _post_control(self, path: str) -> None:
        await self._control_request("POST", path)

    async def _control_request(self, method: str, path: str) -> httpx.Response:
        wrapper = self._client._client_wrapper
        url = f"{wrapper.get_base_url().rstrip('/')}{path}"
        async with httpx.AsyncClient(timeout=30.0) as http:
            response = await http.request(method, url, headers=wrapper.get_headers())
            response.raise_for_status()
            return response
