"""Use the released human REST client with transient Bearer credentials."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import httpx
from band_rest import AsyncRestClient
from band_rest.core.api_error import ApiError
from band_rest.types.chat_message_request import ChatMessageRequest
from band_rest.types.chat_message_request_mentions_item import (
    ChatMessageRequestMentionsItem,
)

from bandteams.auth import Tokens
from bandteams.proposals import Refused, Rejected
from bandteams.settings import HOSTED_REST, REQUEST_SECONDS, Binding


class Bearer(httpx.Auth):
    """Replace generated API-key authentication at HTTPX's public seam."""

    def __init__(self, tokens: Tokens) -> None:
        self.token = tokens.access_token

    def auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers.pop("x-api-key", None)
        request.headers["Authorization"] = f"Bearer {self.token.get_secret_value()}"
        yield request


class Band:
    """Qualify one person's profile, membership and explicitly confirmed writes."""

    def __init__(
        self, tokens: Tokens, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.http = httpx.AsyncClient(
            auth=Bearer(tokens),
            transport=transport,
            timeout=REQUEST_SECONDS,
            follow_redirects=False,
        )
        self.rest = AsyncRestClient(
            api_key="", base_url=HOSTED_REST, httpx_client=self.http
        )

    async def identity(self, person: Binding) -> None:
        profile = await self.rest.human_api_profile.get_my_profile(
            request_options={"max_retries": 0}
        )
        if profile.data.id != person.band_user_id:
            raise Refused("Signed-in Band identity does not match the manifest")

    async def participants(self, person: Binding, room: str, recipient: str) -> None:
        cursor = None
        seen: set[str] = set()
        members: dict[str, Any] = {}
        while True:
            options: dict[str, Any] = {
                "limit": 100,
                "request_options": {"max_retries": 0},
            }
            if cursor:
                options["cursor"] = cursor
            page = await self.rest.human_api_participants.list_my_chat_participants(
                room, **options
            )
            members.update({member.id: member for member in page.data})
            if page.metadata.has_more is False:
                break
            cursor = page.metadata.next_cursor
            if not cursor or cursor in seen:
                raise Refused("Incomplete Band roster")
            seen.add(cursor)
        if any(
            key not in members or members[key].status != "active"
            for key in (person.band_user_id, recipient)
        ):
            raise Refused("Band membership or recipient is no longer eligible")
        if recipient == person.band_user_id:
            raise Refused("Self recipients are forbidden")

    async def send(self, room: str, recipient: str, body: str) -> str:
        try:
            result = await self.rest.human_api_messages.send_my_chat_message(
                room,
                message=ChatMessageRequest(
                    content=body,
                    mentions=[ChatMessageRequestMentionsItem(id=recipient)],
                ),
                request_options={"max_retries": 0},
            )
        except ApiError as error:
            if error.status_code in (400, 401, 403, 404, 422, 429):
                raise Rejected("Band rejected the send") from error
            raise
        if not result.data.success or not result.data.id:
            raise ValueError("Band acceptance was not confirmed")
        return result.data.id

    async def close(self) -> None:
        await self.http.aclose()
