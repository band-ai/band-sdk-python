"""Kit: wire vocabulary, request projection and the hub-room double."""

from __future__ import annotations

import json
from typing import Any

import httpx

from band.platform.event import MessageEvent

REST_URL = "https://test.invalid"
AGENT_API_URL = f"{REST_URL}/api/v1/agent"
HUB_ROOM_ID = "hub-room-1"
STAMP = "2026-02-09T10:30:00Z"


def sent(request: httpx.Request) -> tuple[str, str, Any]:
    """The method, URL and JSON body of one request the platform received."""
    body = json.loads(request.content) if request.content else None
    return request.method, str(request.url), body


class HubRoom:
    """The hub room's execution queue: records each injected event's text."""

    def __init__(self) -> None:
        self.received: list[tuple[str, str | None]] = []

    async def inject(self, room_id: str, event: MessageEvent) -> None:
        assert event.payload is not None
        self.received.append((room_id, event.payload.content))
