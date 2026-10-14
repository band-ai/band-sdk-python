"""A posting HTTP peer for lifecycle tests through the real host scheduler."""

from __future__ import annotations

import json

import httpx

from tests.runtime.helpers import ALICE, LifecyclePlatform, page


class ReplyingPlatform(LifecyclePlatform):
    def __init__(self) -> None:
        super().__init__()
        self.replies: list[str] = []
        self.errors: list[str] = []

    def answer(self, request: httpx.Request) -> httpx.Response:
        resource = request.url.path.rsplit("/", 1)[-1]
        if resource == "participants":
            return page([{**ALICE, "role": "member", "status": "active"}])
        if request.method == "POST" and resource in {"messages", "events"}:
            payload = json.loads(request.content)
            if resource == "messages":
                self.replies.append(payload["message"]["content"])
                result = {"id": "reply", "recipients": [], "success": True}
            else:
                event = payload["event"]
                if event["message_type"] == "error":
                    self.errors.append(event["content"])
                result = {
                    "id": "event",
                    "message_type": event["message_type"],
                    "success": True,
                }
            return httpx.Response(200, json={"data": result})
        return super().answer(request)
