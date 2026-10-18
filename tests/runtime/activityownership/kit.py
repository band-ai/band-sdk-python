from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from tests.runtime.helpers import ALICE, LifecyclePlatform, page


class ActivityPlatform(LifecyclePlatform):
    def __init__(self) -> None:
        super().__init__()
        self.activity_bodies: list[dict[str, Any]] = []
        self.hold_next_activity = False
        self.activity_entered = asyncio.Event()
        self.activity_release = asyncio.Event()

    async def respond(self, request: httpx.Request) -> httpx.Response:
        response = self.answer(request)
        if self.hold_next_activity and request.url.path.endswith("/activity"):
            self.hold_next_activity = False
            self.activity_entered.set()
            await self.activity_release.wait()
        return response

    def answer(self, request: httpx.Request) -> httpx.Response:
        tail = request.url.path.rsplit("/", 1)[-1]
        if tail == "activity":
            self.activity_bodies.append(json.loads(request.content))
        if tail == "participants":
            return page([{**ALICE, "role": "member", "status": "active"}])
        if request.method == "POST" and tail == "messages":
            return httpx.Response(
                201, json={"data": {"id": "m-1", "success": True, "recipients": []}}
            )
        return super().answer(request)
