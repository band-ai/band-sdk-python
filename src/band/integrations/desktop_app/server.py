"""Local stdio MCP App that renders a live Band room transcript."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable

from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server
from mcp.types import Resource, TextContent, Tool
from pydantic import BaseModel

from band.client.rest import AsyncRestClient
from band.integrations.desktop_app.event_relay import DesktopRoomEventRelay
from band.integrations.desktop_app.logs import configure as configure_logging
from band.integrations.desktop_app.prompts import (
    JOIN_TOOL_DESCRIPTION,
    MONITOR_TOOL_DESCRIPTION,
    REFRESH_TOOL_DESCRIPTION,
    SERVER_INSTRUCTIONS,
    VIEW_RESOURCE_DESCRIPTION,
    join_summary,
    monitor_summary,
    refresh_summary,
    wake_prompt,
)
from band.integrations.desktop_app.room import AgentIdentity, RoomTranscript
from band.integrations.desktop_app.service import (
    AgentTranscriptTools,
    RoomTranscriptService,
)
from band.integrations.desktop_app.settings import DesktopRoomViewSettings
from band.integrations.desktop_app.tools import (
    JoinRoomInput,
    RefreshRoomInput,
    RoomTool,
    WaitForRoomEventInput,
)
from band.integrations.desktop_app.view import room_view_fingerprint, room_view_html

logger = logging.getLogger(__name__)

# The wire contract with Claude Desktop and the mounted view. Not tunable:
# renaming any of these can only break the host's ability to talk to us.
# The URI carries the document's own fingerprint because Desktop caches the
# resource by URI — any asset change rolls the URI and busts that cache.
ROOM_VIEW_URI = f"ui://band/room-transcript/{room_view_fingerprint()}"
ROOM_VIEW_MIME_TYPE = "text/html;profile=mcp-app"
UI_EXTENSION_ID = "io.modelcontextprotocol/ui"


async def _join(
    service: RoomTranscriptService,
    arguments: dict[str, Any],
) -> tuple[str, RoomTranscript]:
    parsed = JoinRoomInput.model_validate(arguments)
    chat_id = await service.resolve_room(parsed.chat_id)
    transcript = await service.read(chat_id)
    service.wakes.suppress(chat_id, transcript.pending_requests)
    logger.info(
        "join chat=%s messages=%d pending=%d transport=%s",
        chat_id,
        len(transcript.messages),
        len(transcript.pending_requests),
        transcript.transport.role,
    )
    return join_summary(transcript, requested=parsed.chat_id), transcript


async def _refresh(
    service: RoomTranscriptService,
    arguments: dict[str, Any],
) -> tuple[str, RoomTranscript]:
    parsed = RefreshRoomInput.model_validate(arguments)
    transcript = await service.read(parsed.chat_id, since=parsed.since)
    return refresh_summary(transcript), transcript


async def _monitor(
    service: RoomTranscriptService,
    arguments: dict[str, Any],
) -> tuple[str, RoomTranscript]:
    parsed = WaitForRoomEventInput.model_validate(arguments)
    service.release_wakes(parsed.chat_id, parsed.retry_wakes)
    event = await service.wait_for_room_event(
        parsed.chat_id,
        since=parsed.since,
        timeout_seconds=(
            parsed.timeout_seconds or service.tuning.band_room_event_timeout_s
        ),
    )
    event.wake_requests = service.wakes.claim(parsed.chat_id, event.pending_requests)
    if event.wake_requests:
        event.wake_prompt = wake_prompt(parsed.chat_id, event.wake_requests)
    tick = logger.info if event.messages or event.wake_requests else logger.debug
    tick(
        "tick chat=%s event=%s messages=%d pending=%d wakes=%s",
        parsed.chat_id,
        event.event_received,
        len(event.messages),
        len(event.pending_requests),
        [message.id for message in event.wake_requests],
    )
    summary = monitor_summary(
        event,
        elsewhere=service.unannounced_rooms(parsed.chat_id),
    )
    # A quiet tick repeats every few seconds, so it sheds what the caller holds.
    return summary, (event if event.messages else event.tick())


ToolHandler = Callable[
    [RoomTranscriptService, dict[str, Any]],
    Awaitable[tuple[str, RoomTranscript]],
]

TOOL_HANDLERS: dict[str, ToolHandler] = {
    RoomTool.JOIN: _join,
    RoomTool.REFRESH: _refresh,
    RoomTool.MONITOR: _monitor,
}


def _connected_host(server: Server[Any, Any]) -> Any:
    """The initialize params of the host driving this call, if there is one.

    Reading them needs an active request context, which a direct handler call
    in a test does not have.
    """
    try:
        return server.request_context.session.client_params
    except LookupError:
        return None


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    schema.pop("title", None)
    return schema


def room_view_tools() -> list[Tool]:
    """The one-time join, nonvisual waiter, and app-only refresh."""
    shared_meta = {
        "ui": {"resourceUri": ROOM_VIEW_URI},
        "ui/resourceUri": ROOM_VIEW_URI,
    }
    return [
        Tool(
            name=RoomTool.JOIN,
            description=JOIN_TOOL_DESCRIPTION,
            inputSchema=_schema(JoinRoomInput),
            _meta=shared_meta,
        ),
        Tool(
            name=RoomTool.REFRESH,
            description=REFRESH_TOOL_DESCRIPTION,
            inputSchema=_schema(RefreshRoomInput),
            # No resourceUri: a host renders the results of any tool that names
            # one, which would redeliver this app-initiated result as a second
            # ui/notifications/tool-result and remount the room view.
            _meta={"ui": {"visibility": ["app"]}},
        ),
        Tool(
            name=RoomTool.MONITOR,
            description=MONITOR_TOOL_DESCRIPTION,
            inputSchema=_schema(WaitForRoomEventInput),
            _meta={
                "ui": {"visibility": ["model", "app"]},
            },
        ),
    ]


def create_server(service: RoomTranscriptService) -> Server[Any, Any]:
    """Build the local stdio server around an injectable transcript reader."""
    server: Server[Any, Any] = Server(
        "band-room-view",
        instructions=SERVER_INSTRUCTIONS,
    )
    tools = {tool.name: tool for tool in room_view_tools()}

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return list(tools.values())

    @server.call_tool(validate_input=True)
    async def call_tool(
        tool_name: str,
        arguments: dict[str, Any],
    ) -> tuple[list[TextContent], dict[str, Any]]:
        service.capture_host(_connected_host(server))
        handler = TOOL_HANDLERS.get(tool_name)
        if handler is None:
            raise ValueError(f"Unknown tool: {tool_name}")
        summary, transcript = await handler(service, arguments)
        return (
            [TextContent(type="text", text=summary)],
            transcript.model_dump(mode="json"),
        )

    @server.list_resources()
    async def list_resources() -> list[Resource]:
        return [
            Resource(
                uri=ROOM_VIEW_URI,
                name="Band room transcript",
                description=VIEW_RESOURCE_DESCRIPTION,
                mimeType=ROOM_VIEW_MIME_TYPE,
            )
        ]

    @server.read_resource()
    async def read_resource(uri: Any) -> list[ReadResourceContents]:
        if str(uri) != ROOM_VIEW_URI:
            raise ValueError(f"Unknown resource: {uri}")
        return [
            ReadResourceContents(
                content=room_view_html(),
                mime_type=ROOM_VIEW_MIME_TYPE,
            )
        ]

    return server


@asynccontextmanager
async def connected_agent_service(
    agent_key: str,
    rest_url: str,
    ws_url: str,
) -> AsyncIterator[RoomTranscriptService]:
    """Keep the Desktop-owned Band agent online while MCP is running."""
    bootstrap_tools = AgentTranscriptTools(
        AsyncRestClient(api_key=agent_key, base_url=rest_url.rstrip("/"))
    )
    profile = await bootstrap_tools.get_agent_profile()
    agent_id = str(profile.get("id") or "")
    if not agent_id:
        raise ValueError("Band agent /me response did not include an agent ID.")

    relay = DesktopRoomEventRelay(
        agent_id=agent_id,
        agent_key=agent_key,
        rest_url=rest_url.rstrip("/"),
        ws_url=ws_url,
    )
    try:
        await relay.start()
        yield RoomTranscriptService(
            bootstrap_tools,
            viewer=AgentIdentity.model_validate(profile),
            events=relay.events,
            transport=relay.status,
        )
    finally:
        await relay.stop()


async def run(settings: DesktopRoomViewSettings | None = None) -> None:
    """Serve the transcript app over the stdio connection Desktop owns."""
    resolved = settings or DesktopRoomViewSettings()
    agent_key, rest_url, ws_url = resolved.resolve_connection()
    async with connected_agent_service(agent_key, rest_url, ws_url) as service:
        server = create_server(service)
        initialization_options = server.create_initialization_options(
            experimental_capabilities={
                UI_EXTENSION_ID: {"mimeTypes": [ROOM_VIEW_MIME_TYPE]}
            }
        )
        async with stdio_server() as streams:
            await server.run(streams[0], streams[1], initialization_options)


def entry_point() -> None:
    """Console-script entry point."""
    configure_logging()
    asyncio.run(run())
