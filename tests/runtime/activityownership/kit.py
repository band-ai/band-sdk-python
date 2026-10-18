from __future__ import annotations

import asyncio
import json
from itertools import groupby
from typing import Any

import httpx

from tests.runtime.helpers import PostingPlatform


class ActivityPlatform(PostingPlatform):
    def __init__(self) -> None:
        super().__init__()
        self.activity_bodies: list[dict[str, Any]] = []
        self.hold_next_activity = False
        self.activity_entered = asyncio.Event()
        self.activity_release = asyncio.Event()

    @property
    def working_transitions(self) -> list[bool]:
        return [
            working
            for working, _ in groupby(body["working"] for body in self.activity_bodies)
        ]

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
        return super().answer(request)
