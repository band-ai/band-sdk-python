"""Script the external Gemini generateContent transport."""

from __future__ import annotations

import json
from collections import deque
from typing import Any

import httpx
from pydantic import BaseModel

CUSTOM = "customdecline"
SUCCESS = "settled"
FAILURE = "decline rejected"


class CustomDeclineInput(BaseModel):
    """Settle this input without a reply."""

    reason: str


class GeneratePeer:
    def __init__(self) -> None:
        self.responses: deque[list[dict[str, Any]]] = deque()
        self.requests: list[dict[str, Any]] = []

    def answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        content = self.responses.popleft()
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"role": "model", "parts": content},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 3},
            },
        )


def call(
    name: str = CUSTOM,
    *,
    arguments: dict[str, Any] | None = None,
    call_id: str = "decline",
) -> dict[str, Any]:
    return {
        "functionCall": {
            "id": call_id,
            "name": name,
            "args": {"reason": "FYI"} if arguments is None else arguments,
        }
    }


WORK = "work"


class WorkInput(BaseModel):
    """Do companion work in the current batch."""
