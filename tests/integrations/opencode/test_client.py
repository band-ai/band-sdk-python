"""Tests for HttpOpencodeClient against a real in-process ASGI OpenCode fake.

Uses ``httpx.ASGITransport`` (a first-class httpx testing transport) over a
small Starlette app that stands in for the real OpenCode HTTP+SSE server, so
requests flow through the client's real request-building/parsing code against
a real ASGI request/response cycle rather than a hand-rolled mock.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from band.integrations.opencode.client import HttpOpencodeClient


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
                    "/session/{session_id}/permissions/{permission_id}",
                    self._reply_permission,
                    methods=["POST"],
                ),
                Route(
                    "/question/{request_id}/reply",
                    self._reply_question,
                    methods=["POST"],
                ),
                Route(
                    "/question/{request_id}/reject",
                    self._reject_question,
                    methods=["POST"],
                ),
                Route(
                    "/session/{session_id}/abort", self._abort_session, methods=["POST"]
                ),
                Route("/mcp", self._register_mcp, methods=["POST"]),
                Route("/mcp/{name}", self._deregister_mcp, methods=["DELETE"]),
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

    async def _reject_question(self, request: Request) -> Response:
        await self._record(request)
        return Response(status_code=200)

    async def _abort_session(self, request: Request) -> Response:
        await self._record(request)
        return Response(status_code=200)

    async def _register_mcp(self, request: Request) -> Response:
        await self._record(request)
        return JSONResponse({"ok": True})

    async def _deregister_mcp(self, request: Request) -> Response:
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
                for data_line in frame["data"].splitlines():
                    lines.append(f"data: {data_line}")
                lines.append("")
                yield ("\n".join(lines) + "\n").encode()

        return StreamingResponse(body(), media_type="text/event-stream")


@pytest.fixture
def fake_server() -> FakeOpencodeServer:
    return FakeOpencodeServer()


def make_client(fake_server: FakeOpencodeServer, **kwargs: Any) -> HttpOpencodeClient:
    return HttpOpencodeClient(
        base_url="http://opencode.test",
        transport=httpx.ASGITransport(app=fake_server.app),
        **kwargs,
    )


async def test_create_session_omits_title_when_not_given(
    fake_server: FakeOpencodeServer,
) -> None:
    client = make_client(fake_server)
    try:
        session = await client.create_session()
        assert session == {"id": "sess-new", "title": "created"}
        assert fake_server.requests[-1]["body"] is None
    finally:
        await client.close()


async def test_create_session_sends_title_when_given(
    fake_server: FakeOpencodeServer,
) -> None:
    client = make_client(fake_server)
    try:
        await client.create_session(title="My Session")
        assert fake_server.requests[-1]["body"] == {"title": "My Session"}
    finally:
        await client.close()


async def test_get_session_returns_payload_for_known_session(
    fake_server: FakeOpencodeServer,
) -> None:
    client = make_client(fake_server)
    try:
        session = await client.get_session("sess-existing")
        assert session == {"id": "sess-existing"}
    finally:
        await client.close()


async def test_get_session_raises_http_status_error_on_404(
    fake_server: FakeOpencodeServer,
) -> None:
    client = make_client(fake_server)
    try:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await client.get_session("sess-missing")
        assert exc_info.value.response.status_code == 404
    finally:
        await client.close()


async def test_directory_and_workspace_set_query_params_and_headers(
    fake_server: FakeOpencodeServer,
) -> None:
    client = make_client(
        fake_server, directory="/tmp/project", workspace="my-workspace"
    )
    try:
        await client.create_session()
        request = fake_server.requests[-1]
        assert request["query"] == {
            "directory": "/tmp/project",
            "workspace": "my-workspace",
        }
        assert request["headers"]["x-opencode-directory"] == "/tmp/project"
        assert request["headers"]["x-opencode-workspace"] == "my-workspace"
    finally:
        await client.close()


async def test_non_ascii_directory_header_is_percent_encoded(
    fake_server: FakeOpencodeServer,
) -> None:
    directory = "/tmp/pröject"
    client = make_client(fake_server, directory=directory)
    try:
        await client.create_session()
        request = fake_server.requests[-1]
        assert request["headers"]["x-opencode-directory"] == quote(directory)
        # The raw directory still travels correctly in the query param, which
        # httpx encodes for the URL itself rather than as a raw header value.
        assert request["query"]["directory"] == directory
    finally:
        await client.close()


async def test_no_directory_or_workspace_omits_query_params(
    fake_server: FakeOpencodeServer,
) -> None:
    client = make_client(fake_server)
    try:
        await client.create_session()
        assert fake_server.requests[-1]["query"] == {}
    finally:
        await client.close()


async def test_prompt_async_sends_only_parts_when_optional_fields_absent(
    fake_server: FakeOpencodeServer,
) -> None:
    client = make_client(fake_server)
    try:
        await client.prompt_async(
            "sess-existing", parts=[{"type": "text", "text": "hi"}]
        )
        assert fake_server.requests[-1]["body"] == {
            "parts": [{"type": "text", "text": "hi"}]
        }
    finally:
        await client.close()


async def test_prompt_async_sends_all_optional_fields_when_given(
    fake_server: FakeOpencodeServer,
) -> None:
    client = make_client(fake_server)
    try:
        await client.prompt_async(
            "sess-existing",
            parts=[{"type": "text", "text": "hi"}],
            system="be helpful",
            model={"providerID": "opencode", "modelID": "some-model"},
            agent="build",
            variant="fast",
        )
        assert fake_server.requests[-1]["body"] == {
            "parts": [{"type": "text", "text": "hi"}],
            "system": "be helpful",
            "model": {"providerID": "opencode", "modelID": "some-model"},
            "agent": "build",
            "variant": "fast",
        }
    finally:
        await client.close()


async def test_reply_permission_posts_response_to_permission_path(
    fake_server: FakeOpencodeServer,
) -> None:
    client = make_client(fake_server)
    try:
        await client.reply_permission("sess-existing", "perm-1", response="once")
        request = fake_server.requests[-1]
        assert request["path"] == "/session/sess-existing/permissions/perm-1"
        assert request["body"] == {"response": "once"}
    finally:
        await client.close()


async def test_reply_question_posts_answers(fake_server: FakeOpencodeServer) -> None:
    client = make_client(fake_server)
    try:
        await client.reply_question("req-1", answers=[["blue"], ["yes"]])
        request = fake_server.requests[-1]
        assert request["path"] == "/question/req-1/reply"
        assert request["body"] == {"answers": [["blue"], ["yes"]]}
    finally:
        await client.close()


async def test_reject_question_posts_to_reject_path(
    fake_server: FakeOpencodeServer,
) -> None:
    client = make_client(fake_server)
    try:
        await client.reject_question("req-1")
        assert fake_server.requests[-1]["path"] == "/question/req-1/reject"
    finally:
        await client.close()


async def test_abort_session_posts_to_abort_path(
    fake_server: FakeOpencodeServer,
) -> None:
    client = make_client(fake_server)
    try:
        await client.abort_session("sess-existing")
        assert fake_server.requests[-1]["path"] == "/session/sess-existing/abort"
    finally:
        await client.close()


async def test_register_mcp_server_sends_remote_config(
    fake_server: FakeOpencodeServer,
) -> None:
    client = make_client(fake_server)
    try:
        await client.register_mcp_server(name="band", url="http://127.0.0.1:9999/sse")
        assert fake_server.requests[-1]["body"] == {
            "name": "band",
            "config": {"type": "remote", "url": "http://127.0.0.1:9999/sse"},
        }
    finally:
        await client.close()


async def test_deregister_mcp_server_deletes_by_name(
    fake_server: FakeOpencodeServer,
) -> None:
    client = make_client(fake_server)
    try:
        await client.deregister_mcp_server("band")
        request = fake_server.requests[-1]
        assert request["method"] == "DELETE"
        assert request["path"] == "/mcp/band"
    finally:
        await client.close()


async def test_iter_events_injects_missing_type_from_event_name(
    fake_server: FakeOpencodeServer,
) -> None:
    fake_server.sse_frames = [
        {
            "event": "session.idle",
            "id": "evt-1",
            "data": json.dumps({"properties": {"sessionID": "sess-existing"}}),
        }
    ]
    client = make_client(fake_server)
    try:
        events = [event async for event in client.iter_events()]
        assert events == [
            {"properties": {"sessionID": "sess-existing"}, "type": "session.idle"}
        ]
    finally:
        await client.close()


async def test_iter_events_preserves_explicit_type_field(
    fake_server: FakeOpencodeServer,
) -> None:
    fake_server.sse_frames = [
        {"data": json.dumps({"type": "message.updated", "properties": {}})}
    ]
    client = make_client(fake_server)
    try:
        events = [event async for event in client.iter_events()]
        assert events == [{"type": "message.updated", "properties": {}}]
    finally:
        await client.close()


async def test_iter_events_handles_multiline_data_payloads(
    fake_server: FakeOpencodeServer,
) -> None:
    payload = json.dumps(
        {"type": "message.part.updated", "properties": {"n": 1}}, indent=2
    )
    fake_server.sse_frames = [{"data": payload}]
    client = make_client(fake_server)
    try:
        events = [event async for event in client.iter_events()]
        assert events == [{"type": "message.part.updated", "properties": {"n": 1}}]
    finally:
        await client.close()


async def test_iter_events_skips_malformed_json_and_continues(
    fake_server: FakeOpencodeServer,
) -> None:
    fake_server.sse_frames = [
        {"event": "broken", "data": "{not valid json"},
        {"event": "session.idle", "data": json.dumps({"properties": {}})},
    ]
    client = make_client(fake_server)
    try:
        events = [event async for event in client.iter_events()]
        assert events == [{"properties": {}, "type": "session.idle"}]
    finally:
        await client.close()


async def test_iter_events_resumes_with_last_event_id_header(
    fake_server: FakeOpencodeServer,
) -> None:
    fake_server.sse_frames = [
        {
            "event": "session.idle",
            "id": "evt-42",
            "data": json.dumps({"properties": {}}),
        }
    ]
    client = make_client(fake_server)
    try:
        async for _event in client.iter_events():
            pass

        fake_server.sse_frames = []
        async for _event in client.iter_events():
            pass

        assert fake_server.requests[-1]["path"] == "/event"
        assert fake_server.requests[-1]["headers"]["last-event-id"] == "evt-42"
    finally:
        await client.close()


async def test_close_prevents_further_requests(fake_server: FakeOpencodeServer) -> None:
    client = make_client(fake_server)
    await client.close()

    with pytest.raises(RuntimeError, match="client has been closed"):
        await client.get_session("sess-existing")
