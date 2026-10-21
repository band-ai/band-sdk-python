from __future__ import annotations

import asyncio
import json

import httpx

from tests.runtime.helpers import ALICE, LifecyclePlatform, page


def is_message_post(request: httpx.Request) -> bool:
    return request.method == "POST" and request.url.path.endswith("/messages")


def message_content(request: httpx.Request) -> str:
    return json.loads(request.content)["message"]["content"]


class ActivityPlatform(LifecyclePlatform):
    """A running room whose participants are listed and whose messages land."""

    def __init__(self) -> None:
        super().__init__()
        self.room_messages: list[str] = []

    async def respond(self, request: httpx.Request) -> httpx.Response:
        return self.answer(request)

    def answer(self, request: httpx.Request) -> httpx.Response:
        tail = request.url.path.rsplit("/", 1)[-1]
        if tail == "participants":
            return page([{**ALICE, "role": "member", "status": "active"}])
        if is_message_post(request):
            self.room_messages.append(message_content(request))
            return httpx.Response(
                201, json={"data": {"id": "m-1", "success": True, "recipients": []}}
            )
        return super().answer(request)


class HeldReplyPlatform(ActivityPlatform):
    """Holds the post of one reply until ``release`` is set, so its turn can be
    kept in the delivery tail after the model has finished."""

    def __init__(self, held_reply: str) -> None:
        super().__init__()
        self.held_reply = held_reply
        self.reply_entered = asyncio.Event()
        self.release = asyncio.Event()

    async def respond(self, request: httpx.Request) -> httpx.Response:
        if is_message_post(request) and message_content(request) == self.held_reply:
            self.reply_entered.set()
            await self.release.wait()
        return self.answer(request)
