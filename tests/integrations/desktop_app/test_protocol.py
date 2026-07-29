"""The stdio MCP surface: the tools, the mounted view, and the host behind it."""

from __future__ import annotations

import re
import shutil
import subprocess
from importlib.resources import as_file, files
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp import types

from band.integrations.desktop_app.event_relay import RoomEventBroker
from band.integrations.desktop_app.settings import MAX_ROOM_EVENT_TIMEOUT_S
from band.integrations.desktop_app.server import (
    ROOM_VIEW_MIME_TYPE,
    ROOM_VIEW_URI,
    connected_agent_service,
    room_view_tools,
)
from band.integrations.desktop_app.tools import RoomTool
from band.integrations.desktop_app.view import room_view_fingerprint, room_view_html
from tests.integrations.desktop_app.conftest import ROOM_ID, message


class TestToolSurface:
    def test_the_view_uri_tracks_the_document_it_serves(self) -> None:
        """A stale cached view is invisible until someone notices wrong behaviour."""
        assert ROOM_VIEW_URI.endswith(f"/{room_view_fingerprint()}")

    def test_each_tool_declares_who_may_call_it(self) -> None:
        tools = {tool.name: tool for tool in room_view_tools()}

        assert set(tools) == {
            RoomTool.JOIN,
            RoomTool.CREATE,
            RoomTool.REFRESH,
            RoomTool.MONITOR,
        }
        join_meta = tools[RoomTool.JOIN].meta
        assert join_meta is not None
        assert join_meta["ui"]["resourceUri"] == ROOM_VIEW_URI
        assert join_meta["ui/resourceUri"] == ROOM_VIEW_URI
        assert tools[RoomTool.CREATE].meta == join_meta, (
            "every workflow ending in OPEN_ROOM must mount the same live view"
        )
        assert tools[RoomTool.REFRESH].meta == {"ui": {"visibility": ["app"]}}, (
            "a host renders the result of any tool naming a resourceUri, so an "
            "app-only refresh must not name one or it remounts the view"
        )
        assert tools[RoomTool.MONITOR].meta == {
            "ui": {"visibility": ["model", "app"]}
        }, "the view drives the display loop; the model drives the watch"
        assert "keep calling it" in (tools[RoomTool.MONITOR].description or "")

    async def test_a_join_returns_the_transcript_and_the_view(self, room: Any) -> None:
        live = room([message("m-1", "2026-01-01T00:00:01Z")])

        joined = await live.join()
        resource = await live.server.request_handlers[types.ReadResourceRequest](
            types.ReadResourceRequest(
                params=types.ReadResourceRequestParams(uri=ROOM_VIEW_URI)
            )
        )

        assert joined["chat_id"] == ROOM_ID
        assert joined["messages"][0]["id"] == "m-1"
        assert joined["viewer"]["id"] == "agent-1"
        assert isinstance(resource.root, types.ReadResourceResult)
        contents = resource.root.contents[0]
        assert isinstance(contents, types.TextResourceContents)
        assert contents.mimeType == ROOM_VIEW_MIME_TYPE
        assert contents.text == room_view_html()


class TestMountedView:
    def test_the_script_parses(self) -> None:
        """The script ships as a file, so a syntax error is caught here, not live."""
        node = shutil.which("node")
        if node is None:
            pytest.skip("node is not installed")
        script = files("band.integrations.desktop_app.assets") / "room-view.js"
        with as_file(script) as path:
            assert subprocess.run([node, "--check", path]).returncode == 0

    def test_it_is_self_contained(self) -> None:
        """The sandbox blocks every external origin, so nothing may be fetched."""
        assert "https://" not in room_view_html()

    def test_it_speaks_the_app_protocol_and_the_room_tools(self) -> None:
        document = room_view_html()

        for expected in (
            "band_refresh_room_view",
            "band_wait_for_room_event",
            "ui/initialize",
            "ui/notifications/initialized",
            "ui/notifications/size-changed",
            "ui/update-model-context",
            "notifications/message",
            "hostCapabilities",
        ):
            assert expected in document, f"the view no longer uses {expected}"

    def test_it_outlasts_the_longest_wait_the_server_can_choose(self) -> None:
        """The view's own RPC deadline must not fire while the server is still
        legitimately blocked on the room."""
        assert f"maxWatchS: {MAX_ROOM_EVENT_TIMEOUT_S}" in room_view_html()

    def test_it_resumes_on_the_server_cursor(self) -> None:
        """The newest message's timestamp stalls: it does not move when the
        room is quiet, so an empty room would re-read REST on every tick and
        successive calls would be byte-identical."""
        document = room_view_html()

        assert "payload.next_since" in document
        assert "since: cursor" in document
        assert "newestTimestamp" not in document, (
            "a locally tracked timestamp is the cursor the server replaced"
        )

    def test_it_starts_over_when_the_room_changes(self) -> None:
        """Transcript, cursor and briefing all belong to one room, and the
        server serves whichever room a call names."""
        document = room_view_html()

        assert "function enterRoom" in document
        assignments = [
            line.strip()
            for line in document.splitlines()
            if re.search(r"(?<!let )\bchatId = ", line)
        ]
        assert assignments == ["chatId = id;"], (
            "the room id changes in one place, so everything it invalidates is "
            "always discarded with it"
        )

    def test_it_holds_no_state_the_server_owns(self) -> None:
        """Anything the view decides for itself can drift from the server."""
        document = room_view_html()

        assert "payload.role_briefing" in document
        assert "payload.wake_requests" in document
        assert "retry_wakes: offered" in document
        assert "triggeredMessageIds" not in document, (
            "a local trigger set would let a redelivered payload wake twice"
        )

    def test_it_authors_no_model_facing_text(self) -> None:
        """Prompt text in the view is a second copy that drifts from prompts.py."""
        document = room_view_html()

        assert "payload.wake_prompt" in document, (
            "the wake message must be relayed from the server, not composed here"
        )
        for authored in (
            "connected Band agent",
            "band_join_room",
            "untrusted",
            "safety and approval",
        ):
            assert authored not in document, (
                f"the view is writing model-facing text: {authored!r}"
            )


class TestHost:
    async def test_the_declared_capabilities_are_recorded(self, room: Any) -> None:
        """Whether the host advertises sampling decides which designs are possible."""
        live = room([])

        live.service.capture_host(
            SimpleNamespace(
                clientInfo=SimpleNamespace(name="claude-desktop", version="1.2.3"),
                capabilities=SimpleNamespace(
                    sampling=object(),
                    elicitation=None,
                    roots=None,
                    tasks=None,
                    experimental=None,
                ),
            )
        )

        assert live.service.host.name == "claude-desktop"
        assert live.service.host.can_be_woken is True
        assert (await live.read()).host.sampling is True


class TestLifecycle:
    async def test_the_agent_is_kept_online_by_the_sdk_presence_stack(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Band shows the agent online only while this relay holds its socket."""
        bootstrap_rest = MagicMock()
        bootstrap_rest.agent_api_identity.get_agent_me = AsyncMock(
            return_value={"data": {"id": "agent-1", "name": "tom"}}
        )
        relay = MagicMock()
        relay.events = RoomEventBroker()
        relay.start = AsyncMock()
        relay.stop = AsyncMock()
        relay_factory = MagicMock(return_value=relay)
        monkeypatch.setattr(
            "band.integrations.desktop_app.server.AsyncRestClient",
            MagicMock(return_value=bootstrap_rest),
        )
        monkeypatch.setattr(
            "band.integrations.desktop_app.server.DesktopRoomEventRelay",
            relay_factory,
        )

        async with connected_agent_service(
            "agent-key",
            "https://platform.example/",
            "wss://platform.example/socket",
        ) as service:
            assert (await service.viewer()).name == "tom"

        relay_factory.assert_called_once_with(
            agent_id="agent-1",
            agent_key="agent-key",
            rest_url="https://platform.example",
            ws_url="wss://platform.example/socket",
        )
        relay.start.assert_awaited_once()
        relay.stop.assert_awaited_once()
