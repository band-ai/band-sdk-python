"""Local HTTP peer and call helpers for terminal-write settlement tests."""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from band.client.rest import AsyncRestClient
from band.core.settlement import SettlementResult
from band.platform.link import BandLink
from band.platform.message_lifecycle import (
    PROCESSING_ATTEMPT_POINTER,
    VALIDATION_ERROR_CODE,
)

ROOM_ID = "room-1"
MESSAGE_ID = "msg-1"
PEER_URL = "http://peer.test"


@dataclass(frozen=True)
class Reply:
    """One scripted HTTP response; ``body`` is sent verbatim."""

    status: int
    body: str = ""

    @classmethod
    def of_json(cls, status: int, payload: Any) -> Reply:
        return cls(status=status, body=json.dumps(payload))


def error_body(
    code: str, details: dict[str, Any] | None = None
) -> dict[str, dict[str, Any]]:
    """The platform's public error envelope."""
    return {
        "error": {
            "code": code,
            "message": "Validation failed",
            "details": {} if details is None else details,
            "request_id": "request-1",
        }
    }


def accepted(action: str, *, success: bool = True) -> Reply:
    return Reply.of_json(
        200,
        {
            "data": {
                "id": MESSAGE_ID,
                "attempt_number": 1,
                "status": action,
                "success": success,
            }
        },
    )


MISSING_ATTEMPT = Reply.of_json(
    422,
    error_body(
        VALIDATION_ERROR_CODE,
        {PROCESSING_ATTEMPT_POINTER: ["No active processing attempt."]},
    ),
)


class SettlementPeer:
    """Serve one scripted reply to every lifecycle write and record the writes.

    A ``None`` reply holds the request open until the caller is cancelled.
    """

    def __init__(self) -> None:
        self.reply: Reply | None = None
        self.writes: list[str] = []
        self.arrived = asyncio.Event()
        self.app = Starlette(
            routes=[Route("/{path:path}", self.respond, methods=["POST"])]
        )

    async def respond(self, request: Request) -> Response:
        self.writes.append(request.url.path.split("/messages/")[1])
        self.arrived.set()
        if self.reply is None:
            await asyncio.Event().wait()
            raise AssertionError("an unanswered request was released")
        return Response(
            self.reply.body,
            status_code=self.reply.status,
            media_type="application/json",
        )

    @asynccontextmanager
    async def client(self) -> AsyncIterator[AsyncRestClient]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app)
        ) as http:
            yield AsyncRestClient(
                api_key="test-key", base_url=PEER_URL, httpx_client=http
            )


@asynccontextmanager
async def refused_rest_client() -> AsyncIterator[AsyncRestClient]:
    """A real client aimed at a port nothing listens on."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    async with httpx.AsyncClient() as http:
        yield AsyncRestClient(
            api_key="test-key", base_url=f"http://127.0.0.1:{port}", httpx_client=http
        )


def link_over(rest: AsyncRestClient) -> BandLink:
    link = BandLink(agent_id="agent-123", api_key="test-key", rest_url=PEER_URL)
    link.rest = rest
    return link


@dataclass(frozen=True)
class Terminal:
    """One terminal write, reachable through both the detailed and bool APIs."""

    action: str

    async def detailed(self, link: BandLink) -> SettlementResult:
        if self.action == "processed":
            return await link.mark_processed_result(ROOM_ID, MESSAGE_ID)
        return await link.mark_failed_result(ROOM_ID, MESSAGE_ID, "boom")

    async def legacy(self, link: BandLink) -> bool:
        if self.action == "processed":
            return await link.mark_processed(ROOM_ID, MESSAGE_ID)
        return await link.mark_failed(ROOM_ID, MESSAGE_ID, "boom")

    @property
    def write(self) -> str:
        return f"{MESSAGE_ID}/{self.action}"
