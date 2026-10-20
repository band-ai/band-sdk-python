from __future__ import annotations

import asyncio
import json
from collections import deque
from typing import Any

import httpx

from band.platform.activity import API_VERSION_HEADER
from tests.activitysupport import SUPPORTED_VERSION

DETAIL = {"kind": "tool_call", "name": "Bash"}


class ActivityPeer:
    """The activity endpoint, advertising ``version`` on every response."""

    def __init__(self) -> None:
        self.version: str | None = SUPPORTED_VERSION
        self.reports: list[dict[str, Any]] = []
        self.requests: list[httpx.Request] = []
        self.responses: deque[httpx.Response] = deque()
        self.stall_post_number: int | None = None

    async def respond(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(request)
        self.reports.append(body)
        if len(self.reports) == self.stall_post_number:
            await asyncio.Event().wait()
        response = (
            self.responses.popleft()
            if self.responses
            else httpx.Response(200, json={"data": {"working": body["working"]}})
        )
        if self.version is not None:
            response.headers[API_VERSION_HEADER] = self.version
        return response
