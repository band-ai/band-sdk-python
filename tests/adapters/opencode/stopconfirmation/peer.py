"""An HTTP/SSE peer with gated submissions and independently delivered idle events."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from band.adapters.opencode.adapter import MCP_REGISTRATION_CONNECTED_STATUS
from band.core.types import AgentInput
from band.testing import FakeAgentTools
from tests.adapters.hold import Hold
from tests.adapters.opencode.helpers import batch_input, make_platform_message
from tests.adapters.opencode.resume.kit import answer, room_tools


class EventStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.frames: asyncio.Queue[tuple[dict[str, Any], asyncio.Event]] = (
            asyncio.Queue()
        )
        self.closed = asyncio.Event()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while True:
            event, handled = await self.frames.get()
            yield f"data: {json.dumps(event)}\n\n".encode()
            handled.set()

    async def aclose(self) -> None:
        self.closed.set()

    def enqueue(self, event: dict[str, Any]) -> asyncio.Event:
        handled = asyncio.Event()
        self.frames.put_nowait((event, handled))
        return handled


class OpencodePeer:
    """Keep abort acknowledgment separate from the server's terminal evidence."""

    def __init__(self) -> None:
        self.events = EventStream()
        self.first_prompt = Hold()
        self.fail_first = False
        self.sessions: list[str] = []
        self.prompts: list[str] = []
        self.aborted = asyncio.Event()

    async def request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        match request.method, path:
            case "GET", "/event":
                self.events.closed.clear()
                return httpx.Response(
                    200,
                    stream=self.events,
                    headers={"Content-Type": "text/event-stream"},
                )
            case "GET", _ if path.startswith("/session/"):
                session = path.split("/")[2]
                assert session in self.sessions
                return httpx.Response(200, json={"id": session})
            case "POST", "/mcp":
                name = json.loads(request.content)["name"]
                return httpx.Response(
                    200, json={name: {"status": MCP_REGISTRATION_CONNECTED_STATUS}}
                )
            case "POST", "/session":
                session = f"session-{len(self.sessions) + 1}"
                self.sessions.append(session)
                return httpx.Response(200, json={"id": session})
            case "POST", _ if path.startswith("/mcp/") and path.endswith("/disconnect"):
                return httpx.Response(200, json={})
            case "POST", _ if path.endswith("/prompt_async"):
                session = path.split("/")[2]
                self.prompts.append(session)
                if len(self.prompts) == 1:
                    await self.first_prompt.wait()
                    if self.fail_first:
                        return httpx.Response(
                            500, json={"error": "injected submission failure"}
                        )
                else:
                    for event in answer(session, f"reply-{len(self.prompts)}"):
                        self.events.enqueue(event)
                return httpx.Response(204)
            case "POST", _ if path.endswith("/abort"):
                self.aborted.set()
                return httpx.Response(200, json=True)
            case _:
                raise AssertionError(f"Unexpected request: {request.method} {path}")


def turn(room: str = "room-1", tools: FakeAgentTools | None = None) -> AgentInput:
    return batch_input(
        [make_platform_message(room_id=room)],
        tools or room_tools(),
        is_session_bootstrap=True,
    )
