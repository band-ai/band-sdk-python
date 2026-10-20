from __future__ import annotations

import asyncio
import json
from collections import deque
from typing import Any

import httpx

VERSION_URL = "https://platform.test/api/version"


class ActivityPeer:
    def __init__(self) -> None:
        self.version: object = {"agent_version": "1.25.0"}
        self.version_status = 200
        self.version_content: bytes | None = None
        self.version_requests: list[httpx.Request] = []
        self.reports: list[dict[str, Any]] = []
        self.responses: deque[httpx.Response] = deque()
        self.post_entered = asyncio.Event()
        self.post_release = asyncio.Event()
        self.hold_post_number: int | None = None
        self.discovering = asyncio.Event()
        self.discovery_release: asyncio.Event | None = None

    async def discover(self, request: httpx.Request) -> httpx.Response:
        self.version_requests.append(request)
        self.discovering.set()
        if self.discovery_release is not None:
            await self.discovery_release.wait()
        if self.version_content is not None:
            return httpx.Response(self.version_status, content=self.version_content)
        return httpx.Response(self.version_status, json=self.version)

    async def respond(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.reports.append(body)
        if len(self.reports) == self.hold_post_number:
            self.post_entered.set()
            await self.post_release.wait()
        if self.responses:
            return self.responses.popleft()
        return httpx.Response(200, json={"data": {"working": body["working"]}})
