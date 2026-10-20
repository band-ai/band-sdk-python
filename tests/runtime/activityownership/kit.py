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
        self.activity_cancelled = asyncio.Event()
        self.reject_detail = False
        self.event_bodies: list[dict[str, Any]] = []

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
            try:
                await self.activity_release.wait()
            except asyncio.CancelledError:
                self.activity_cancelled.set()
                raise
        return response

    def assert_hides(self, *private: str) -> None:
        serialized = json.dumps(self.activity_bodies)
        for value in private:
            assert value not in serialized

    def assert_no_tool_narration(self) -> None:
        assert not any(
            body.get("message_type") in {"tool_call", "tool_result"}
            for body in self.event_bodies
        )

    def answer(self, request: httpx.Request) -> httpx.Response:
        tail = request.url.path.rsplit("/", 1)[-1]
        if tail == "events" and request.method == "POST":
            self.event_bodies.append(json.loads(request.content))
        if tail == "activity":
            body = json.loads(request.content)
            self.activity_bodies.append(body)
            if self.reject_detail and "activity" in body:
                return httpx.Response(422, json={"error": "unsupported activity"})
        return super().answer(request)
