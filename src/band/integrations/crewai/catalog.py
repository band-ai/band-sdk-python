"""The Band platform tools CrewAI exposes, one ``@band_tool`` body each.

``band_tool`` carries the lifecycle every tool would otherwise repeat --
report the call, run the body, report the result, render the model-facing
text -- so a body spells out only the platform call it makes. The decorated
bodies collect into ``PLATFORM_TOOLS`` in declaration order, which is the
order a crew is handed them.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, cast

from pydantic import BaseModel, field_validator

from band.core.protocols import AgentToolsProtocol
from band.integrations.crewai.reporting import CrewAIToolReporter
from band.runtime.tools import (
    BandTool,
    is_mcp_content_result,
    platform_args_schema,
    serialize_tool_result,
    validate_tool_arguments,
)


# --- Rendering a tool result as the text CrewAI hands the model ---


def serialize_success_result(result: Any) -> str:
    """Serialize a successful tool result without losing domain status fields.

    Pydantic models are converted via serialize_tool_result at the boundary.
    Dicts that already carry a "status" key (e.g. domain status from REST
    responses) get that field renamed to "result_status" so the wrapper's
    own "status": "success" envelope stays unambiguous.
    """
    result = serialize_tool_result(result)
    if isinstance(result, dict):
        payload = dict(result)
        result_status = payload.pop("status", None)
        response: dict[str, Any] = {"status": "success", **payload}
        if result_status is not None:
            response["result_status"] = result_status
        return json.dumps(response, default=str)
    return json.dumps({"status": "success", "result": result}, default=str)


def as_json(result: Any) -> str:
    """Serialize an envelope the body already shaped."""
    return json.dumps(result, default=str)


def vision_sentinel(result: dict[str, Any]) -> str:
    """Encode the first image block as CrewAI's ``VISION_IMAGE:`` sentinel.

    StepExecutor rewrites this exact string shape into a real image content
    block before the LLM sees it: an internal crewai protocol, verified against
    the installed package rather than documented, so a version bump can break it.
    """
    block = result["content"][0]
    return f"VISION_IMAGE:{block['mimeType']}:{block['data']}"


def text_or_success(result: Any) -> str:
    """Pass a body-rendered string through; wrap anything else in the envelope."""
    return result if isinstance(result, str) else serialize_success_result(result)


def succeeded(message: str, **fields: Any) -> dict[str, Any]:
    """The success envelope for a tool whose result is the fact that it ran."""
    return {"status": "success", "message": message, **fields}


# --- Tool declaration ---


@dataclass(frozen=True)
class Invocation:
    """The per-call handles a tool body runs against."""

    tools: AgentToolsProtocol
    reporter: CrewAIToolReporter


ToolBody = Callable[..., Awaitable[Any]]
Renderer = Callable[[Any], str]
ArgNormalizer = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class ToolSpec:
    """One platform tool: what CrewAI advertises, plus the body behind it."""

    name: BandTool
    body: ToolBody
    args_schema: type[BaseModel]
    declared: dict[str, Any] | None
    render: Renderer
    reports: bool
    normalize: ArgNormalizer | None

    def arguments(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """The call's arguments: declared defaults filled in, unknown keys dropped."""
        if self.declared is None:
            return kwargs
        known = {key: kwargs[key] for key in kwargs.keys() & self.declared.keys()}
        args = {**self.declared, **known}
        return self.normalize(args) if self.normalize else args

    async def invoke(self, call: Invocation, kwargs: dict[str, Any]) -> str:
        args = self.arguments(kwargs)
        if self.reports:
            await call.reporter.report_call(call.tools, self.name, args)
        result = await self.body(call, **args)
        if self.reports:
            await call.reporter.report_result(call.tools, self.name, result)
        return self.render(result)


PLATFORM_TOOLS: list[ToolSpec] = []


def band_tool(
    name: BandTool,
    *,
    args_schema: type[BaseModel] | None = None,
    render: Renderer = serialize_success_result,
    reports: bool = True,
    normalize: ArgNormalizer | None = None,
) -> Callable[[ToolBody], ToolSpec]:
    """Register an async body as the platform tool ``name``.

    The body's keyword-only parameters and their defaults are the arguments the
    tool accepts and reports; a body declaring ``**kwargs`` instead receives the
    caller's arguments untouched (``band_send_event`` validates them itself).
    ``reports=False`` hands a body full control of its own event emission.
    """

    def register(body: ToolBody) -> ToolSpec:
        spec = ToolSpec(
            name=name,
            body=body,
            args_schema=args_schema or platform_args_schema(name),
            declared=_declared_arguments(body),
            render=render,
            reports=reports,
            normalize=normalize,
        )
        PLATFORM_TOOLS.append(spec)
        return spec

    return register


def _declared_arguments(body: ToolBody) -> dict[str, Any] | None:
    """A body's keyword parameters and defaults, or None if it takes raw kwargs."""
    params = list(inspect.signature(body).parameters.values())[1:]
    if any(param.kind is param.VAR_KEYWORD for param in params):
        return None
    return {param.name: param.default for param in params}


def without_none(**fields: Any) -> dict[str, Any]:
    """Only the fields that carry a value — the REST layer rejects explicit nulls."""
    return {key: value for key, value in fields.items() if value is not None}


# --- CrewAI-specific parsing leniency ---


def normalize_mentions_lenient(value: Any) -> list[str]:
    """Coerce whatever CrewAI's tool layer produced into a list of handles.

    Smaller models driving CrewAI emit ``mentions`` as a JSON-encoded string or
    a bracketed list of bare handles (``"[@john/agent]"``) rather than a real
    list. Without this the platform rejects the call for having no mentions and
    the agent retries in a loop, so the leniency lives here rather than on the
    master model, which every other adapter satisfies as-is.
    """
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(decoded, list):
                return [str(item) for item in decoded]
        stripped = value.strip().strip("[]")
        return [
            token.strip().strip("'\"") for token in stripped.split(",") if token.strip()
        ]
    return [str(value)]


SEND_MESSAGE_ARGS_SCHEMA: type[BaseModel] = platform_args_schema(
    BandTool.SEND_MESSAGE,
    validators={
        "normalize_mentions": field_validator("mentions", mode="before")(
            staticmethod(normalize_mentions_lenient)
        ),
    },
)

# band_send_room_file's mentions field is the same shape/purpose as
# band_send_message's -- smaller models hit the same leniency need here.
SEND_ROOM_FILE_ARGS_SCHEMA: type[BaseModel] = platform_args_schema(
    BandTool.SEND_ROOM_FILE,
    validators={
        "normalize_mentions": field_validator("mentions", mode="before")(
            staticmethod(normalize_mentions_lenient)
        ),
    },
)

SEND_EVENT_ARGS_SCHEMA: type[BaseModel] = platform_args_schema(BandTool.SEND_EVENT)


def _posted_file_args(args: dict[str, Any]) -> dict[str, Any]:
    """Apply the schema's mention/caption leniency to a directly-called ``_run``."""
    return {
        **args,
        "caption": args["caption"] or "",
        "mentions": normalize_mentions_lenient(args["mentions"]),
    }


# --- Chat tools ---


@band_tool(
    BandTool.SEND_MESSAGE,
    args_schema=SEND_MESSAGE_ARGS_SCHEMA,
    render=as_json,
    reports=False,
)
async def _send_message(
    call: Invocation, *, content: str = "", mentions: Any = None
) -> Any:
    """Post a message, unless the reporter owns delivery (and its events) itself."""
    # Normalized here too, not just in the schema: _run is also called
    # directly, bypassing args_schema validation.
    mention_list = normalize_mentions_lenient(mentions)
    delivery = getattr(call.reporter, "execute_send_message", None)
    if callable(delivery):
        deliver = cast(
            Callable[[AgentToolsProtocol, str, list[str]], Awaitable[None]], delivery
        )
        await deliver(call.tools, content, mention_list)
    else:
        await call.reporter.report_call(
            call.tools,
            BandTool.SEND_MESSAGE,
            {"content": content, "mentions": mention_list},
        )
        await call.tools.send_message(content, mention_list)
        await call.reporter.report_result(call.tools, BandTool.SEND_MESSAGE, "success")
    return succeeded("Message sent")


@band_tool(
    BandTool.SEND_EVENT,
    args_schema=SEND_EVENT_ARGS_SCHEMA,
    render=as_json,
    reports=False,
)
async def _send_event(call: Invocation, **kwargs: Any) -> Any:
    """Emit a raw event, reporting nothing itself to avoid meta-events."""
    # Validated here rather than by the caller: _run is also called directly,
    # bypassing args_schema, and _execute_tool turns the ValueError into the
    # error result the caller expects.
    args = validate_tool_arguments(BandTool.SEND_EVENT, SEND_EVENT_ARGS_SCHEMA, kwargs)
    await call.tools.send_event(
        args["content"], args["message_type"], metadata=args.get("metadata")
    )
    return succeeded("Event sent")


@band_tool(BandTool.ADD_PARTICIPANT)
async def _add_participant(
    call: Invocation, *, identifier: str = "", role: str = "member"
) -> Any:
    return await call.tools.add_participant(identifier, role)


@band_tool(BandTool.REMOVE_PARTICIPANT)
async def _remove_participant(call: Invocation, *, identifier: str = "") -> Any:
    return await call.tools.remove_participant(identifier)


@band_tool(BandTool.GET_PARTICIPANTS, render=as_json)
async def _get_participants(call: Invocation) -> Any:
    participants = await call.tools.get_participants()
    serialized, count = _plain_participants(participants)
    return {"status": "success", "participants": serialized, "count": count}


def _plain_participants(participants: Any) -> tuple[Any, int]:
    """Participants as plain data plus their count (0 when not a list)."""
    if not isinstance(participants, list):
        return participants, 0
    plain = [p.model_dump() if hasattr(p, "model_dump") else p for p in participants]
    return plain, len(participants)


@band_tool(BandTool.LOOKUP_PEERS)
async def _lookup_peers(call: Invocation, *, page: int = 1, page_size: int = 50) -> Any:
    return await call.tools.lookup_peers(page, page_size)


@band_tool(BandTool.CREATE_CHATROOM, render=as_json)
async def _create_chatroom(call: Invocation, *, task_id: str | None = None) -> Any:
    room_id = await call.tools.create_chatroom(task_id)
    return succeeded("Chat room created", room_id=room_id)


# --- Contact tools ---


@band_tool(BandTool.LIST_CONTACTS)
async def _list_contacts(
    call: Invocation, *, page: int = 1, page_size: int = 50
) -> Any:
    return await call.tools.list_contacts(page, page_size)


@band_tool(BandTool.ADD_CONTACT)
async def _add_contact(
    call: Invocation, *, handle: str = "", message: str | None = None
) -> Any:
    return await call.tools.add_contact(handle, message)


@band_tool(BandTool.REMOVE_CONTACT)
async def _remove_contact(
    call: Invocation, *, handle: str | None = None, contact_id: str | None = None
) -> Any:
    return await call.tools.remove_contact(handle, contact_id)


@band_tool(BandTool.LIST_CONTACT_REQUESTS)
async def _list_contact_requests(
    call: Invocation,
    *,
    page: int = 1,
    page_size: int = 50,
    sent_status: str = "pending",
) -> Any:
    return await call.tools.list_contact_requests(page, page_size, sent_status)


@band_tool(BandTool.RESPOND_CONTACT_REQUEST)
async def _respond_contact_request(
    call: Invocation,
    *,
    action: str = "",
    handle: str | None = None,
    request_id: str | None = None,
) -> Any:
    return await call.tools.respond_contact_request(action, handle, request_id)


# --- Memory tools ---


@band_tool(BandTool.LIST_MEMORIES)
async def _list_memories(
    call: Invocation,
    *,
    subject_id: str | None = None,
    scope: str | None = None,
    system: str | None = None,
    type: str | None = None,
    segment: str | None = None,
    content_query: str | None = None,
    page_size: int = 50,
    status: str | None = None,
) -> Any:
    return await call.tools.list_memories(
        page_size=page_size,
        **without_none(
            subject_id=subject_id,
            scope=scope,
            system=system,
            type=type,
            segment=segment,
            content_query=content_query,
            status=status,
        ),
    )


@band_tool(BandTool.STORE_MEMORY)
async def _store_memory(
    call: Invocation,
    *,
    content: str = "",
    system: str = "",
    type: str = "",
    segment: str = "",
    thought: str = "",
    scope: str = "",
    subject_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Any:
    return await call.tools.store_memory(
        content=content,
        system=system,
        type=type,
        segment=segment,
        thought=thought,
        scope=scope,
        **without_none(subject_id=subject_id, metadata=metadata),
    )


@band_tool(BandTool.GET_MEMORY)
async def _get_memory(call: Invocation, *, memory_id: str = "") -> Any:
    return await call.tools.get_memory(memory_id)


@band_tool(BandTool.SUPERSEDE_MEMORY)
async def _supersede_memory(call: Invocation, *, memory_id: str = "") -> Any:
    return await call.tools.supersede_memory(memory_id)


@band_tool(BandTool.ARCHIVE_MEMORY)
async def _archive_memory(call: Invocation, *, memory_id: str = "") -> Any:
    return await call.tools.archive_memory(memory_id)


# --- Room file tools ---


@band_tool(BandTool.LIST_ROOM_FILES)
async def _list_room_files(call: Invocation, *, cursor: str | None = None) -> Any:
    return await call.tools.list_room_files(cursor)


@band_tool(BandTool.READ_ROOM_FILE, render=text_or_success)
async def _read_room_file(call: Invocation, *, file_id: str = "") -> Any:
    result = await call.tools.read_room_file(file_id)
    # The sentinel is reported as well as returned: a tool_result carrying the
    # raw base64 blob is worthless to a reader and huge on the wire.
    return vision_sentinel(result) if is_mcp_content_result(result) else result


@band_tool(
    BandTool.SEND_ROOM_FILE,
    args_schema=SEND_ROOM_FILE_ARGS_SCHEMA,
    normalize=_posted_file_args,
)
async def _send_room_file(
    call: Invocation,
    *,
    content: str = "",
    filename: str = "",
    caption: str = "",
    mentions: Any = None,
) -> Any:
    return await call.tools.send_room_file(content, filename, caption, mentions)


if frozenset(spec.name for spec in PLATFORM_TOOLS) != frozenset(BandTool):
    raise ValueError(
        "CrewAI platform tools drifted from BandTool: "
        f"{frozenset(spec.name for spec in PLATFORM_TOOLS) ^ frozenset(BandTool)}"
    )
