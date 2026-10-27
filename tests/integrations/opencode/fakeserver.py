"""Peer for the OpenCode client tests: an in-process ASGI fake of the
OpenCode HTTP+SSE API."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route


class FakeOpencodeServer:
    """In-process fake of the OpenCode HTTP+SSE API, recording every request."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.sse_frames: list[dict[str, str]] = []
        self.known_session_ids: set[str] = {"sess-existing"}
        self.app = Starlette(
            routes=[
                Route("/session", self._create_session, methods=["POST"]),
                Route("/session/{session_id}", self._get_session, methods=["GET"]),
                Route(
                    "/session/{session_id}/prompt_async",
                    self._prompt_async,
                    methods=["POST"],
                ),
                Route(
                    "/permission/{permission_id}/reply",
                    self._reply_permission,
                    methods=["POST"],
                ),
                Route(
                    "/question/{request_id}/reply",
                    self._reply_question,
                    methods=["POST"],
                ),
                Route(
                    "/session/{session_id}/abort", self._abort_session, methods=["POST"]
                ),
                Route("/mcp", self._register_mcp, methods=["POST"]),
                Route("/mcp/{name}/disconnect", self._disconnect_mcp, methods=["POST"]),
                Route("/event", self._event_stream, methods=["GET"]),
            ]
        )

    async def _record(self, request: Request) -> dict[str, Any]:
        body = await request.body()
        entry: dict[str, Any] = {
            "method": request.method,
            "path": request.url.path,
            "query": dict(request.query_params),
            "headers": dict(request.headers),
            "body": json.loads(body) if body else None,
        }
        self.requests.append(entry)
        return entry

    async def _create_session(self, request: Request) -> Response:
        await self._record(request)
        return JSONResponse({"id": "sess-new", "title": "created"})

    async def _get_session(self, request: Request) -> Response:
        await self._record(request)
        session_id = request.path_params["session_id"]
        if session_id not in self.known_session_ids:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse({"id": session_id})

    async def _prompt_async(self, request: Request) -> Response:
        await self._record(request)
        return Response(status_code=200)

    async def _reply_permission(self, request: Request) -> Response:
        await self._record(request)
        return Response(status_code=200)

    async def _reply_question(self, request: Request) -> Response:
        await self._record(request)
        return Response(status_code=200)

    async def _abort_session(self, request: Request) -> Response:
        await self._record(request)
        return Response(status_code=200)

    async def _register_mcp(self, request: Request) -> Response:
        await self._record(request)
        return JSONResponse({"ok": True})

    async def _disconnect_mcp(self, request: Request) -> Response:
        await self._record(request)
        return Response(status_code=200)

    async def _event_stream(self, request: Request) -> Response:
        await self._record(request)

        async def body() -> AsyncIterator[bytes]:
            for frame in self.sse_frames:
                lines: list[str] = []
                if "event" in frame:
                    lines.append(f"event: {frame['event']}")
                if "id" in frame:
                    lines.append(f"id: {frame['id']}")
                for data_line in frame.get("data", "").splitlines():
                    lines.append(f"data: {data_line}")
                lines.append("")
                yield ("\n".join(lines) + "\n").encode()

        return StreamingResponse(body(), media_type="text/event-stream")
