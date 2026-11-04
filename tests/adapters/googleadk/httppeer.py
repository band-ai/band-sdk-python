"""Serve scripted Google HTTP responses through the actual provider client."""

from __future__ import annotations

import asyncio
import json
from enum import StrEnum
from typing import Any

import httpx
from google.genai import Client, types

from tests.adapters.genaikit import (
    PlatformSchemaFakeTools,
)
from tests.framework_conformance.turnprobes import (
    DispatchingFakeTools,
)

LOCAL_API_KEY = "local-test-key"

EFFECT_ARGS = {"content": "Completed effect", "mentions": ["@alice"]}


class HTTPMode(StrEnum):
    MOCK = "mock"
    TCP = "tcp"
    PROXY = "proxy"


class RecoveryTools(DispatchingFakeTools, PlatformSchemaFakeTools):
    """Advertise platform schemas and execute the observable fake tool effects."""


class GoogleHTTPPeer:
    """Script provider HTTP responses while retaining the real ADK tool loop."""

    def __init__(self) -> None:
        self.responses: list[int | types.GenerateContentResponse | None] = []
        self.requests: list[httpx.Request] = []
        self.clients: list[Client] = []
        self.initial_delay = 0.001
        self.max_delay = 0.001
        self.retry_started = asyncio.Event()

    def respond(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self.responses.pop(0)
        assert response is not None, "Disconnects require the TCP peer"
        if isinstance(response, int):
            return httpx.Response(
                response,
                json={"error": {"code": response, "message": "Provider unavailable"}},
            )
        return httpx.Response(
            200, json=response.model_dump(mode="json", by_alias=True, exclude_none=True)
        )

    @property
    def generation_requests(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.requests]

    @property
    def clients_closed(self) -> bool:
        return bool(self.clients) and all(
            client._api_client._httpx_client.is_closed
            and client._api_client._async_httpx_client.is_closed
            for client in self.clients
        )

    async def serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            request_headers = httpx.Headers(
                line.split(b":", 1)
                for line in headers.split(b"\r\n")[1:]
                if b":" in line
            )
            content = await reader.readexactly(int(request_headers["content-length"]))
            request = httpx.Request(
                "POST",
                "http://localhost/generate",
                headers=request_headers,
                content=content,
            )
            if self.responses[0] is None:
                self.responses.pop(0)
                self.requests.append(request)
                return
            response = self.respond(request)
            writer.write(
                f"HTTP/1.1 {response.status_code} {response.reason_phrase}\r\n"
                "Content-Type: application/json\r\nConnection: close\r\n"
                f"Content-Length: {len(response.content)}\r\n\r\n".encode()
                + response.content
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
