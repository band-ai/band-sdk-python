"""A membership-validating REST peer for automatic delivery tests."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from band.core.exceptions import MessagePostKind

ALICE = "alice-id"
BOB = "bob-id"
CAROL = "carol-id"
AGENT = "agent-id"
ROOM = "room-id"
ANSWER = "The provider's final answer"


def participant(sender_id: str) -> dict[str, str]:
    return {
        "id": sender_id,
        "name": sender_id,
        "handle": sender_id,
        "type": "Agent" if sender_id == AGENT else "User",
        "role": "member",
        "status": "active",
    }


class MembershipPeer:
    """Reject posts naming anyone absent from the current room."""

    def __init__(self, members: list[str]) -> None:
        self.members = members
        self.snapshot: Any = [participant(member) for member in members]
        self.read_status = 200
        self.post_status = 201
        self.posts: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.reads = 0
        self.hold_read = False
        self.read_started = asyncio.Event()
        self.release_read = asyncio.Event()

    @property
    def recipients(self) -> list[list[str]]:
        return [
            [mention["id"] for mention in post["message"]["mentions"]]
            for post in self.posts
        ]

    async def answer(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            self.reads += 1
            self.read_started.set()
            if self.hold_read:
                await self.release_read.wait()
            return httpx.Response(self.read_status, json={"data": self.snapshot})
        post = json.loads(request.content)
        if request.url.path.endswith("/events"):
            self.events.append(post)
            return httpx.Response(
                201,
                json={
                    "data": {
                        "id": "posted-event",
                        "message_type": "event",
                        "success": True,
                    }
                },
            )
        self.posts.append(post)
        recipients = post["message"]["mentions"]
        if any(mention["id"] not in self.members for mention in recipients):
            return httpx.Response(
                422,
                json={
                    "error": {
                        "code": "unprocessable_entity",
                        "message": MessagePostKind.MENTION_NOT_IN_ROOM,
                    }
                },
            )
        if self.post_status != 201:
            return httpx.Response(self.post_status, json={"data": {}})
        return httpx.Response(
            201,
            json={
                "data": {
                    "id": "posted-message",
                    "success": True,
                    "recipients": [
                        {"id": item["id"], "handle": item["id"]} for item in recipients
                    ],
                }
            },
        )
