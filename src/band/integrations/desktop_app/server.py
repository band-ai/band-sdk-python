"""Local stdio MCP App that renders a live Band room transcript."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, AsyncIterator, Awaitable, Callable

from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server
from mcp.types import Resource, TextContent, Tool
from pydantic import BaseModel, ValidationError

from band.client.rest import AsyncRestClient
from band.integrations.desktop_app.event_relay import DesktopRoomEventRelay
from band.integrations.desktop_app.logs import configure as configure_logging
from band.integrations.desktop_app.prompts import (
    CREATE_TOOL_DESCRIPTION,
    JOIN_TOOL_DESCRIPTION,
    MONITOR_TOOL_DESCRIPTION,
    SERVER_INSTRUCTIONS,
    SHOW_TOOL_DESCRIPTION,
    VIEW_RESOURCE_DESCRIPTION,
    invalid_arguments,
    join_summary,
    monitor_summary,
    show_summary,
)
from band.integrations.desktop_app.room import AgentIdentity, RoomTranscript
from band.integrations.desktop_app.service import (
    AgentTranscriptTools,
    RoomTranscriptService,
)
from band.integrations.desktop_app.settings import DesktopRoomViewSettings
from band.integrations.desktop_app.tools import (
    CreateAndOpenRoomInput,
    JoinRoomInput,
    MonitorCaller,
    RoomTool,
    ShowRoomInput,
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


@dataclass
class WorkflowResult:
    """The shared state passed through a tool's ordered success operations."""

    room_id: str
    summary: str = ""
    transcript: RoomTranscript | None = None
    requested_room: str | None = None


async def _join(
    service: RoomTranscriptService,
    arguments: dict[str, Any],
) -> WorkflowResult:
    parsed = JoinRoomInput.model_validate(arguments)
    chat_id = await service.resolve_room(parsed.chat_id)
    service.set_attention(chat_id, parsed.attention)
    return WorkflowResult(room_id=chat_id, requested_room=parsed.chat_id)


async def _create(
    service: RoomTranscriptService,
    arguments: dict[str, Any],
) -> WorkflowResult:
    parsed = CreateAndOpenRoomInput.model_validate(arguments)
    chat_id = await service.create_room(parsed.task_id)
    service.set_attention(chat_id, parsed.attention)
    return WorkflowResult(
        room_id=chat_id,
        summary=f"Created Band room {chat_id}.",
    )


async def _open_room(
    service: RoomTranscriptService,
    result: WorkflowResult,
) -> WorkflowResult:
    await service.refresh_viewer()
    transcript = await service.read(result.room_id)
    logger.info(
        "join chat=%s messages=%d pending=%d transport=%s",
        result.room_id,
        len(transcript.messages),
        len(transcript.pending_requests),
        transcript.transport.role,
    )
    opened = join_summary(
        transcript,
        requested=result.requested_room or result.room_id,
    )
    result.summary = "\n\n".join(filter(None, (result.summary, opened)))
    result.transcript = transcript
    return result


async def _show(
    service: RoomTranscriptService,
    arguments: dict[str, Any],
) -> WorkflowResult:
    parsed = ShowRoomInput.model_validate(arguments)
    transcript = await service.read(parsed.chat_id)
    return WorkflowResult(
        room_id=parsed.chat_id,
        summary=show_summary(transcript),
        transcript=transcript,
    )


async def _monitor(
    service: RoomTranscriptService,
    arguments: dict[str, Any],
) -> WorkflowResult:
    parsed = WaitForRoomEventInput.model_validate(arguments)
    quantum = parsed.timeout_seconds or service.tuning.band_room_event_timeout_s
    # Only the agent may change whose attention the room gets: the view's
    # display loop calls this same tool, and a mode is the user's choice
    # relayed through the model, never the app's.
    if parsed.attention is not None and parsed.caller is MonitorCaller.MODEL:
        service.set_attention(parsed.chat_id, parsed.attention)
    # The call itself is the proof the agent's loop is still running, and the
    # quantum it chose is how long the next one may take to arrive.
    if parsed.caller is MonitorCaller.MODEL:
        service.note_model_tick(parsed.chat_id, quantum=quantum)
    event = await service.wait_for_room_event(
        parsed.chat_id,
        since=parsed.since,
        timeout_seconds=quantum,
    )
    event.superseded = not service.instance_is_current(parsed.chat_id, parsed.instance)
    if service.claim_stale_report(parsed.chat_id, event.monitoring):
        logger.warning(
            "monitor loop stopped chat=%s idle=%.0fs; this room is unwatched "
            "until the agent calls again",
            parsed.chat_id,
            event.monitoring.idle_seconds or 0,
        )
    tick = logger.info if event.messages else logger.debug
    tick(
        "tick chat=%s event=%s messages=%d pending=%d",
        parsed.chat_id,
        event.event_received,
        len(event.messages),
        len(event.pending_requests),
    )
    summary = monitor_summary(
        event,
        elsewhere=service.unannounced_rooms(parsed.chat_id),
    )
    # A quiet tick repeats every few seconds, so it sheds what the caller holds.
    return WorkflowResult(
        room_id=parsed.chat_id,
        summary=summary,
        transcript=event if event.messages else event.tick(),
    )


ToolHandler = Callable[
    [RoomTranscriptService, dict[str, Any]],
    Awaitable[WorkflowResult],
]


class WorkflowOperation(StrEnum):
    """Reusable operations a successful desktop workflow may chain."""

    OPEN_ROOM = "open_room"


SuccessOperation = Callable[
    [RoomTranscriptService, WorkflowResult],
    Awaitable[WorkflowResult],
]

SUCCESS_OPERATIONS: dict[WorkflowOperation, SuccessOperation] = {
    WorkflowOperation.OPEN_ROOM: _open_room,
}


@dataclass(frozen=True)
class DesktopToolSpec:
    """One source for a desktop tool's contract, dispatch, and UI behavior."""

    name: RoomTool
    description: str
    input_model: type[BaseModel]
    handler: ToolHandler
    on_success: tuple[WorkflowOperation, ...] = ()
    visibility: tuple[str, ...] | None = None
    mounts_view: bool = False


TOOL_SPECS: tuple[DesktopToolSpec, ...] = (
    DesktopToolSpec(
        name=RoomTool.JOIN,
        description=JOIN_TOOL_DESCRIPTION,
        input_model=JoinRoomInput,
        handler=_join,
        on_success=(WorkflowOperation.OPEN_ROOM,),
        mounts_view=True,
    ),
    DesktopToolSpec(
        name=RoomTool.CREATE,
        description=CREATE_TOOL_DESCRIPTION,
        input_model=CreateAndOpenRoomInput,
        handler=_create,
        on_success=(WorkflowOperation.OPEN_ROOM,),
        mounts_view=True,
    ),
    DesktopToolSpec(
        name=RoomTool.SHOW,
        description=SHOW_TOOL_DESCRIPTION,
        input_model=ShowRoomInput,
        handler=_show,
        mounts_view=True,
    ),
    DesktopToolSpec(
        name=RoomTool.MONITOR,
        description=MONITOR_TOOL_DESCRIPTION,
        input_model=WaitForRoomEventInput,
        handler=_monitor,
        visibility=("model", "app"),
    ),
)


async def _execute_workflow(
    service: RoomTranscriptService,
    spec: DesktopToolSpec,
    arguments: dict[str, Any],
) -> WorkflowResult:
    result = await spec.handler(service, arguments)
    for operation in spec.on_success:
        result = await SUCCESS_OPERATIONS[operation](service, result)
    if result.transcript is None:
        raise RuntimeError(f"{spec.name} produced no room transcript.")
    return result


def _connected_host(server: Server[Any, Any]) -> Any:
    """The initialize params of the host driving this call, if there is one.

    Reading them needs an active request context, which a direct handler call
    in a test does not have.
    """
    try:
        return server.request_context.session.client_params
    except LookupError:
        return None


def inline(node: Any, definitions: dict[str, Any]) -> Any:
    """A schema node with every internal ``$ref`` replaced by its target."""
    if isinstance(node, list):
        return [inline(item, definitions) for item in node]
    if not isinstance(node, dict):
        return node
    reference = node.get("$ref")
    if reference is None:
        return {key: inline(value, definitions) for key, value in node.items()}
    target = inline(definitions[reference.rsplit("/", 1)[-1]], definitions)
    # What the field said about itself wins over what the type says.
    return target | {key: value for key, value in node.items() if key != "$ref"}


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    """The tool's input contract, flattened into one self-contained document.

    Claude Desktop validates arguments against this itself, and mis-parses a
    schema that carries ``$defs``/``$ref``: adding one enum field cost the
    monitor tool every argument it had, arriving as `chat_id` undefined and
    numbers as strings. Pydantic emits a ``$ref`` for any enum or nested model,
    so the flattening lives here rather than in the shape of the models.
    """
    schema = model.model_json_schema()
    flattened = inline(schema, schema.pop("$defs", {}))
    flattened.pop("title", None)
    return flattened


def room_view_tools() -> list[Tool]:
    """Generate the MCP surface from the desktop workflow registry."""
    tools = []
    for spec in TOOL_SPECS:
        ui: dict[str, Any] = {}
        if spec.mounts_view:
            ui["resourceUri"] = ROOM_VIEW_URI
        if spec.visibility:
            ui["visibility"] = list(spec.visibility)
        meta: dict[str, Any] = {"ui": ui}
        if spec.mounts_view:
            meta["ui/resourceUri"] = ROOM_VIEW_URI
        tools.append(
            Tool(
                name=spec.name,
                description=spec.description,
                inputSchema=_schema(spec.input_model),
                _meta=meta,
            )
        )
    return tools


def create_server(service: RoomTranscriptService) -> Server[Any, Any]:
    """Build the local stdio server around an injectable transcript reader."""
    server: Server[Any, Any] = Server(
        "band-room-view",
        instructions=SERVER_INSTRUCTIONS,
    )
    tools = {tool.name: tool for tool in room_view_tools()}
    specs = {spec.name.value: spec for spec in TOOL_SPECS}

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return list(tools.values())

    # The input models are the only gate. The framework's extra JSON Schema
    # pass reads the host's spelling literally, and Claude Desktop sends
    # numbers as JSON strings — which cost every monitor call that named a
    # timeout, and with it the agent's loop. Pydantic reads "30" as 30, so one
    # lenient layer accepts what a real host sends and still refuses nonsense.
    @server.call_tool(validate_input=False)
    async def call_tool(
        tool_name: str,
        arguments: dict[str, Any],
    ) -> tuple[list[TextContent], dict[str, Any]]:
        service.capture_host(_connected_host(server))
        spec = specs.get(tool_name)
        if spec is None:
            raise ValueError(f"Unknown tool: {tool_name}")
        try:
            result = await _execute_workflow(service, spec, arguments)
        except ValidationError as error:
            logger.warning("rejected %s call: %s", tool_name, error)
            raise ValueError(invalid_arguments(tool_name, error)) from error
        assert result.transcript is not None
        return (
            [TextContent(type="text", text=result.summary)],
            result.transcript.model_dump(mode="json"),
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
