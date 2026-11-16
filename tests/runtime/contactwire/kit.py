"""Kit: wire vocabulary, request projection and the hub-room double."""

from __future__ import annotations

from typing import Any

import httpx

from band.platform.event import MessageEvent
from tests.fakeplatform import AGENT_API_URL, STAMP
from tests.httpwire import request_json

CHATS_URL = f"{AGENT_API_URL}/chats"
HUB_ROOM_ID = "hub-room-1"


HUB_ROOM_CREATED = {
    "data": {
        "id": HUB_ROOM_ID,
        "title": "Contacts hub",
        "inserted_at": STAMP,
        "updated_at": STAMP,
    }
}


def sent(request: httpx.Request) -> tuple[str, str, Any]:
    """The method, URL and JSON body of one request the platform received."""
    return request.method, str(request.url), request_json(request)


class HubRoom:
    """The hub room's execution queue: records each injected event's text."""

    def __init__(self) -> None:
        self.received: list[tuple[str, str | None]] = []

    async def inject(self, room_id: str, event: MessageEvent) -> None:
        assert event.payload is not None
        self.received.append((room_id, event.payload.content))
