"""Room file tools: gating, listing, reading (text/image/binary), sending."""

from __future__ import annotations

import hashlib
import inspect
import json
from typing import Any

import pytest

from band.runtime.tools import (
    FILE_TOOL_NAMES,
    AgentTools,
    describe_tool_result_as_text,
    is_room_posting_tool,
    iter_tool_definitions,
)

ROOM_ID = "room-1"
FILE_ID = "file-1"


class FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        content: bytes = b"",
        headers: dict[str, str] | None = None,
        payload: Any = None,
    ) -> None:
        self.status_code = status_code
        self.content = content if payload is None else json.dumps(payload).encode()
        self.headers = headers or {}
        self._payload = payload

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError(f"unexpected HTTP {self.status_code}")


class FakeHttp:
    """Records requests; answers from a (method, url-substring) route table."""

    def __init__(self, routes: list[tuple[str, str, FakeResponse]]) -> None:
        self.routes = routes
        self.requests: list[dict[str, Any]] = []

    async def _dispatch(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append({"method": method, "url": url, **kwargs})
        for route_method, fragment, response in self.routes:
            if route_method == method and fragment in url:
                return response
        return FakeResponse(payload={"data": []})

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        return await self._dispatch("GET", url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> FakeResponse:
        return await self._dispatch("PUT", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        return await self._dispatch("POST", url, **kwargs)


class FakeRest:
    """Just enough client_wrapper surface for AgentTools._files_transport."""

    class _Inner:
        def __init__(self, http: FakeHttp) -> None:
            self.httpx_client = http

    class _Wrapper:
        def __init__(self, http: FakeHttp) -> None:
            self.httpx_client = FakeRest._Inner(http)

        def get_base_url(self) -> str:
            return "https://platform.test"

        def get_headers(self) -> dict[str, str]:
            return {"X-API-Key": "band_a_test"}

    def __init__(self, http: FakeHttp) -> None:
        self._client_wrapper = FakeRest._Wrapper(http)


def make_tools(
    routes: list[tuple[str, str, FakeResponse]],
    participants: list[dict[str, str]] | None = None,
) -> tuple[AgentTools, FakeHttp]:
    http = FakeHttp(routes)
    tools = AgentTools(ROOM_ID, FakeRest(http), participants=participants or [])
    return tools, http


class TestGating:
    def test_file_tools_are_off_by_default(self) -> None:
        names = {d.name for d in iter_tool_definitions()}
        assert not (FILE_TOOL_NAMES & names)

    def test_include_files_exposes_all_three(self) -> None:
        names = {d.name for d in iter_tool_definitions(include_files=True)}
        assert FILE_TOOL_NAMES <= names

    def test_send_room_file_counts_as_a_room_post(self) -> None:
        assert is_room_posting_tool("band_send_room_file")


class TestListRoomFiles:
    @pytest.mark.asyncio
    async def test_queries_every_delivery_status_view(self) -> None:
        tools, http = make_tools([])
        await tools.list_room_files()
        queries = [request["url"].split("?")[-1] for request in http.requests]
        assert queries == [
            "limit=50&sort_order=desc&status=processing",
            "limit=50&sort_order=desc&status=processed",
            "limit=50&sort_order=desc&status=pending",
            "limit=50&sort_order=desc",
        ]

    @pytest.mark.asyncio
    async def test_renders_descriptors_and_dedupes_across_views(self) -> None:
        message = {
            "id": "m1",
            "sender_name": "Bob",
            "content": "here you go",
            "attachments": [
                {
                    "id": FILE_ID,
                    "name": "notes.txt",
                    "content_type": "text/plain",
                    "bytes": 42,
                }
            ],
        }
        response = FakeResponse(payload={"data": [message]})
        tools, _ = make_tools(
            [
                ("GET", "status=processing", response),
                ("GET", "status=processed", response),
            ]
        )
        listing = await tools.list_room_files()
        assert listing.count(f"file_id={FILE_ID}") == 1
        assert "name=notes.txt" in listing
        assert "from=Bob" in listing

    @pytest.mark.asyncio
    async def test_bare_id_attachments_from_older_platforms_still_render(self) -> None:
        message = {"id": "m1", "sender_type": "User", "attachments": [FILE_ID]}
        tools, _ = make_tools(
            [("GET", "status=processed", FakeResponse(payload={"data": [message]}))]
        )
        listing = await tools.list_room_files()
        assert f"file_id={FILE_ID}" in listing

    @pytest.mark.asyncio
    async def test_empty_room_explains_mention_scoping(self) -> None:
        tools, _ = make_tools([])
        assert "@mention" in await tools.list_room_files()

    @pytest.mark.asyncio
    async def test_asks_the_platform_for_the_newest_page(self) -> None:
        """The listing says "newest first", so it has to ask for newest first.

        The agent message index is oldest-first by default, so a bare
        ``limit=50`` returns the FIRST fifty messages ever addressed to this
        agent. In any room past that, ``reversed()`` cannot recover what was
        never fetched: the tool's own primary use — find the file someone just
        sent me — silently returns the oldest files instead.
        """
        tools, http = make_tools([])
        await tools.list_room_files()

        for request in http.requests:
            assert "sort_order=desc" in request["url"], (
                f"listing asked for the oldest page: {request['url']}"
            )

    @pytest.mark.asyncio
    async def test_keeps_the_newest_files_when_truncating(self) -> None:
        """Ten rows are shown, and they must be the ten newest.

        With a descending fetch the newest message arrives first, so the rows
        are already in the right order and truncation keeps the right end.
        """
        messages = [
            {
                "id": f"m{index}",
                "sender_name": "Bob",
                "content": f"file {index}",
                "attachments": [{"id": f"file-{index}", "name": f"{index}.txt"}],
            }
            # Newest first, the way a descending index answers.
            for index in range(20, 0, -1)
        ]
        tools, _ = make_tools(
            [("GET", "status=processed", FakeResponse(payload={"data": messages}))]
        )

        listing = await tools.list_room_files()

        assert "file-20" in listing, "the newest file was truncated away"
        assert "file-1 " not in listing


class TestReadRoomFile:
    @pytest.mark.asyncio
    async def test_text_file_returns_named_inline_content(self) -> None:
        response = FakeResponse(
            content=b"the cheese is in the cupboard",
            headers={
                "content-type": "text/plain; charset=utf-8",
                "content-disposition": 'attachment; filename="secret.txt"',
            },
        )
        tools, _ = make_tools([("GET", f"/files/{FILE_ID}", response)])
        result = await tools.read_room_file(FILE_ID)
        assert result.startswith("secret.txt (text/plain, 29 bytes)")
        assert "cupboard" in result

    @pytest.mark.asyncio
    async def test_image_returns_mcp_vision_content(self) -> None:
        response = FakeResponse(
            content=b"\x89PNG fake bytes",
            headers={"content-type": "image/png"},
        )
        tools, _ = make_tools([("GET", f"/files/{FILE_ID}", response)])
        result = await tools.read_room_file(FILE_ID)
        assert isinstance(result, dict)
        image_block = result["content"][0]
        assert image_block["type"] == "image"
        assert image_block["mimeType"] == "image/png"

    @pytest.mark.asyncio
    async def test_unknown_binary_is_described_not_dumped(self) -> None:
        response = FakeResponse(
            content=b"%PDF-1.7 ...",
            headers={"content-type": "application/pdf"},
        )
        tools, _ = make_tools([("GET", f"/files/{FILE_ID}", response)])
        result = await tools.read_room_file(FILE_ID)
        assert "application/pdf" in result
        assert "%PDF" not in result

    @pytest.mark.asyncio
    async def test_missing_file_points_at_the_listing_tool(self) -> None:
        tools, _ = make_tools(
            [("GET", f"/files/{FILE_ID}", FakeResponse(status_code=404))]
        )
        assert "band_list_room_files" in await tools.read_room_file(FILE_ID)


class TestSendRoomFile:
    PARTICIPANTS = [{"id": "u-bob", "handle": "bob", "name": "Bob"}]

    def routes(self) -> list[tuple[str, str, FakeResponse]]:
        return [
            ("PUT", "/files", FakeResponse(payload={"data": {"id": FILE_ID}})),
            ("POST", "/messages", FakeResponse(payload={"data": {"id": "m9"}})),
        ]

    @pytest.mark.asyncio
    async def test_uploads_then_attaches_with_resolved_mention(self) -> None:
        tools, http = make_tools(self.routes(), participants=self.PARTICIPANTS)
        result = await tools.send_room_file("plan.txt", "step 1: cheese", "@bob")
        assert FILE_ID in result

        upload, post = http.requests
        body = upload["content"]
        assert upload["headers"]["x-file-name"] == "plan.txt"
        assert upload["headers"]["x-file-sha256"] == hashlib.sha256(body).hexdigest()

        message = post["json"]["message"]
        assert message["attachment_ids"] == [FILE_ID]
        assert message["mentions"][0]["id"] == "u-bob"
        assert message["content"].startswith("@bob ")

    @pytest.mark.asyncio
    async def test_unknown_mention_fails_before_any_upload(self) -> None:
        tools, http = make_tools(self.routes(), participants=self.PARTICIPANTS)
        with pytest.raises(ValueError):
            await tools.send_room_file("plan.txt", "text", "@nobody")
        assert http.requests == []


class TestImageResultsOnNonVisionAdapters:
    """An image read returns MCP content so bridges can give the model vision.

    Only the Claude bridge forwards that shape. CrewAI json-dumps whatever the
    tool returns and pydantic-ai stringifies it, so on those adapters the base64
    payload — up to the 3 MiB inline limit, about 4.2 million characters once
    encoded — lands in the model's context as prose. The shared tool description
    tells the model "Images are shown to you directly", which is true on exactly
    one of the four adapters that now advertise Capability.FILES.
    """

    def test_text_rendering_keeps_the_description_and_drops_the_payload(self) -> None:
        image_result = {
            "content": [
                {"type": "image", "data": "A" * 4_000_000, "mimeType": "image/png"},
                {
                    "type": "text",
                    "text": "The image above is shot.png (image/png, 3000000 bytes). "
                    "Describe what you see in it.",
                },
            ]
        }

        rendered = describe_tool_result_as_text(image_result)

        assert "AAAA" not in rendered
        assert "shot.png" in rendered
        assert "image/png" in rendered


class TestRoomPostingClassification:
    """A file share posts to the room, so it has to count as a reply.

    ``replied`` gates the adapter's "the agent said nothing this turn" error.
    Keying it to one tool name means an agent that answers by sharing a file —
    a real, room-visible response — is reported as having produced no reply,
    and the room gets a spurious error after a successful share.
    ``is_room_posting_tool`` already knows better and is the shared vocabulary
    for exactly this question.
    """

    def test_the_reply_gate_uses_the_shared_room_posting_vocabulary(self) -> None:
        from band.integrations.crewai import tools as crewai_tools

        for name in ("band_send_message", "band_send_room_file"):
            assert is_room_posting_tool(name)

        source = inspect.getsource(crewai_tools._execute_tool)
        assert "is_room_posting_tool" in source, (
            "the reply gate compares against a single tool name instead of the "
            "shared room-posting vocabulary"
        )


class TestMissingFileApi:
    """A platform without the agent file routes is not a missing file.

    Phoenix answers an unrouted path with 404, and read_room_file maps every
    404 to "no such file in this room". On a deployment where the file API is
    not present that answer is false for every id the agent tries, so the agent
    concludes each file is gone rather than that it cannot fetch files at all —
    and the listing tool, which reads a different endpoint, keeps showing them.
    """

    @pytest.mark.asyncio
    async def test_a_missing_route_is_not_reported_as_a_missing_file(self) -> None:
        # Phoenix's own 404 body, which carries no FallbackController shape.
        route_404 = FakeResponse(
            status_code=404,
            payload={"errors": {"detail": "Not Found"}},
        )
        tools, _ = make_tools([("GET", f"/files/{FILE_ID}", route_404)])

        answer = await tools.read_room_file(FILE_ID)

        assert "band_list_room_files" not in answer
        assert "file api" in answer.lower() or "not available" in answer.lower()

    @pytest.mark.asyncio
    async def test_a_real_missing_file_still_points_at_the_listing(self) -> None:
        file_404 = FakeResponse(
            status_code=404,
            payload={"error": {"code": "not_found", "message": "File not found"}},
        )
        tools, _ = make_tools([("GET", f"/files/{FILE_ID}", file_404)])

        answer = await tools.read_room_file(FILE_ID)

        assert "band_list_room_files" in answer
