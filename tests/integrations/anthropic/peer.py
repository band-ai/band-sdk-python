"""Script the external Anthropic Messages transport."""

from __future__ import annotations

import json
from collections import deque
from typing import Any

import httpx2
from pydantic import BaseModel

CUSTOM = "customdecline"
SUCCESS = "settled"
FAILURE = "decline rejected"


class CustomDeclineInput(BaseModel):
    """Settle this input without a reply."""

    reason: str


class MessagesPeer:
    def __init__(self) -> None:
        self.responses: deque[list[dict[str, Any]]] = deque()
        self.requests: list[dict[str, Any]] = []

    def answer(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        content = self.responses.popleft()
        return httpx2.Response(
            200,
            json={
                "id": "message",
                "type": "message",
                "role": "assistant",
                "model": "scripted",
                "content": content,
                "stop_reason": "tool_use"
                if content[0]["type"] == "tool_use"
                else "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 7, "output_tokens": 3},
            },
        )


def call(
    name: str = CUSTOM,
    *,
    arguments: dict[str, Any] | None = None,
    call_id: str = "decline",
) -> dict[str, Any]:
    return {
        "type": "tool_use",
        "id": call_id,
        "name": name,
        "input": {"reason": "FYI"} if arguments is None else arguments,
    }


WORK = "work"


class WorkInput(BaseModel):
    """Do companion work in the current batch."""


def tool_results(request: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        block
        for message in request["messages"]
        if isinstance(message["content"], list)
        for block in message["content"]
        if block.get("type") == "tool_result"
    ]
