"""A gated SSE response for the real Letta client."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

RESULT = "opaque stream result"
RUN = "run-current"
STEP = "step-current"
DATE = "2026-11-01T00:00:00Z"


def frame(message: dict[str, Any]) -> bytes:
    return ("data: " + json.dumps(message) + "\n\n").encode()


def phase(kind: str, **fields: Any) -> dict[str, Any]:
    return {
        "id": kind,
        "date": DATE,
        "message_type": kind,
        "run_id": RUN,
        "step_id": STEP,
        **fields,
    }


class CompactionStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.paused = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = asyncio.Event()
        self.requests: list[dict[str, Any]] = []

    async def respond(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path in (
            "/v1/agents/agent/messages",
            "/v1/conversations/conversation/messages",
        )
        self.requests.append(json.loads(request.content))
        return httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, stream=self
        )

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield frame(phase("event_message", event_type="compaction", event_data={}))
        self.paused.set()
        await self.release.wait()
        yield frame(phase("summary_message", summary="private summary"))
        yield frame(phase("assistant_message", content=RESULT))
        yield frame(
            {
                "message_type": "usage_statistics",
                "prompt_tokens": 11,
                "completion_tokens": 7,
            }
        )
        yield b"data: [DONE]\n\n"

    async def aclose(self) -> None:
        self.closed.set()
