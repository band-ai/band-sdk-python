"""
Parlant tool definitions that wrap Band AgentTools.

These tools are defined at server startup and use a session-keyed registry
to access the current room's tools during execution.

This module provides the same tools as LangGraph/Claude adapters:
- band_send_message: Send messages to the chat room
- band_send_event: Send events (thought, error, task)
- band_add_participant: Add agents/users to the room
- band_remove_participant: Remove participants
- band_lookup_peers: Find available agents
- band_get_participants: List current participants
- band_create_chatroom: Create new rooms
- band_list_contacts: List agent's contacts
- band_add_contact: Send a contact request
- band_remove_contact: Remove an existing contact
- band_list_contact_requests: List received and sent requests
- band_respond_contact_request: Approve, reject, or cancel requests
- band_list_room_files: List files shared in the current room
- band_read_room_file: Read a file shared in the current room
- band_send_room_file: Upload text content as a file and share it

NOTE: We intentionally do NOT use `from __future__ import annotations` here
because Parlant's @p.tool decorator checks annotation types at runtime.
"""

import functools
import inspect
import json
import logging
import warnings
from typing import (
    Annotated,
    Any,
    Callable,
    Literal,
    Optional,
    cast,
    get_args,
    get_origin,
)

from band.core.exceptions import BandToolError
from band.core.task_types import TaskAssignmentStatus, TaskLifecycleState, TaskListState
from band.core.types import AdapterFeatures, Capability
from band.runtime.tools import (
    append_available_mention_handles,
    get_tool_description,
    is_mcp_content_result,
    resolve_tool_model,
    serialize_tool_result,
)

logger = logging.getLogger(__name__)

# Every log line this module emits is tagged with it, so a Parlant run's tool
# activity greps out of a mixed log in one pass.
LOG_PREFIX = "[Parlant Tool]"

# What a tool answers the model with when its Parlant session has no Band room
# bound — a session that outlived its room, or a tool called before one was set.
NO_SESSION_TOOLS_ERROR = "Error: No tools available in current context"

# Longest argument value echoed into the per-call log line; a full message body
# or file payload would otherwise dominate the log.
LOGGED_VALUE_CHARS = 50

# Session-keyed registry to hold tools for each session
# This approach works across async contexts (unlike ContextVar)
_session_tools: dict[str, Any] = {}

# Track whether send_message was called for each session
# This helps the adapter know if it needs to forward Parlant's response
_session_message_sent: dict[str, bool] = {}

# Parlant tools take mentions as a comma-separated string, not the master
# model's list[str], so the master description needs this appended — it is
# genuinely Parlant-specific and not something get_tool_description() covers.
# Phrased without "array"/"list" wording so it doesn't read as contradicting
# the master text's "mentions array" line right above it.
SEND_MESSAGE_MENTIONS_NOTE = (
    "\n\nThis tool's mentions argument is a single string: separate multiple "
    'handles with commas, e.g. "@alice, @bob/agent".'
)

# Same divergence as SEND_MESSAGE_MENTIONS_NOTE, appended to the per-argument
# description instead of the tool-level one: the master field's list-oriented
# text would otherwise reach the LLM unqualified for this comma-separated param.
SEND_MESSAGE_MENTIONS_PARAM_NOTE = (
    " This tool takes it as a single comma-separated string, not a list, "
    'e.g. "@alice, @bob/agent".'
)

# The master model describes lookup_peers' raw return shape (a 'data'/'metadata'
# dict) for adapters that pass it through unchanged. This Parlant tool formats
# that result into a plain-text summary instead, so the master claim would be
# wrong here without this correction.
LOOKUP_PEERS_RETURN_NOTE = (
    "\n\nThis tool returns a formatted text summary of matching agents, not "
    "the 'data'/'metadata' dict described above."
)


def _literal_choices(annotation: Any) -> tuple[str, ...] | None:
    """String choices of a master field's ``Literal[...]`` annotation, if any.

    Parlant's schema builder turns a real ``enum.Enum`` class into a JSON
    Schema ``enum``, but a bare ``Literal[...]`` isn't one — it falls into
    the builder's list-only generic-container branch and raises. So a
    Literal-typed master field can't be passed through as the parameter's own
    annotation; its choices are folded into the description as prose instead.
    """
    if get_origin(annotation) is Literal:
        args = get_args(annotation)
        if args and all(isinstance(a, str) for a in args):
            return args
    return None


class NoSessionTools(Exception):
    """A tool ran while its Parlant session had no Band room bound to it."""


def require_session_tools(context: Any) -> Any:
    """The room's ``AgentTools`` for this Parlant session, or refuse to run.

    Raising rather than returning ``None`` is what lets ``band_tool`` turn the
    refusal into the model-visible error from one place.
    """
    tools = get_session_tools(context.session_id)
    if not tools:
        raise NoSessionTools(context.session_id)
    return tools


def with_mention_handles(message: str, tools: Any) -> str:
    """``message`` plus the handles this room offers, so a bad mention can retry."""
    return append_available_mention_handles(
        message, tools.participants, getattr(tools, "agent_id", None)
    )


def split_mentions(mentions: str) -> list[str]:
    """Parlant's one comma-separated mentions string, as the platform's handle list."""
    return [mention.strip() for mention in mentions.split(",") if mention.strip()]


def _logged_arguments(call: inspect.BoundArguments) -> str:
    """The call's own arguments, truncated, for one per-tool log line."""
    rendered = ", ".join(
        f"{name}={str(value)[:LOGGED_VALUE_CHARS]}"
        for name, value in call.arguments.items()
        if name != "context"
    )
    return f", {rendered}" if rendered else ""


def or_none(value: str) -> str | None:
    """``""`` is how a Parlant model omits a string; the platform wants ``None``."""
    return value or None


def set_session_tools(session_id: str, tools: Optional[Any]) -> None:
    """Set the tools for a specific Parlant session."""
    if tools is None:
        _session_tools.pop(session_id, None)
        _session_message_sent.pop(session_id, None)
    else:
        _session_tools[session_id] = tools
        _session_message_sent[session_id] = False
    logger.debug("Set tools for session %s: %s", session_id, tools is not None)


def get_session_tools(session_id: str) -> Optional[Any]:
    """Get the tools for a specific Parlant session."""
    tools = _session_tools.get(session_id)
    logger.debug(
        "Get tools for session_id=%s: found=%s, available_sessions=%s",
        session_id,
        tools is not None,
        list(_session_tools.keys()),
    )
    return tools


def mark_message_sent(session_id: str) -> None:
    """Mark that a message was sent via the send_message tool for this session."""
    _session_message_sent[session_id] = True
    logger.debug("Marked message sent for session %s", session_id)


def was_message_sent(session_id: str) -> bool:
    """Check if a message was sent via the send_message tool for this session."""
    return _session_message_sent.get(session_id, False)


# Keep old API for backwards compatibility (deprecated)
def set_current_tools(tools: Optional[Any]) -> None:
    """Deprecated: Use set_session_tools instead."""
    warnings.warn(
        "set_current_tools is deprecated, use set_session_tools instead",
        DeprecationWarning,
        stacklevel=2,
    )


def get_current_tools() -> Optional[Any]:
    """Deprecated: Use get_session_tools instead."""
    warnings.warn(
        "get_current_tools is deprecated, use get_session_tools instead",
        DeprecationWarning,
        stacklevel=2,
    )
    return None  # Always returns None, tools now accessed via session_id


def create_parlant_tools(features: AdapterFeatures | None = None) -> list[Any]:
    """Create Parlant tool definitions that wrap Band tools.

    These tools use context variables to access the current room's
    AgentToolsProtocol during execution.

    Args:
        features: Optional adapter features. When CONTACTS capability is absent,
            contact-management tools are excluded from the returned list.

    Returns:
        List of Parlant ToolEntry objects
    """
    try:
        import parlant.sdk as p  # type: ignore[missing-import]
        from parlant.core.tools import (  # type: ignore[missing-import]
            ToolContext,
            ToolParameterOptions,
            ToolResult,
        )
    except ImportError:
        logger.warning("Parlant SDK not installed, skipping tool creation")
        return []

    def describe_from_master(
        func: Callable[..., Any],
        extra_doc: str,
        param_overrides: dict[str, str] | None,
    ) -> None:
        """Give *func* its tool and per-argument text from the master model.

        Parlant's schema builder never reads a docstring's ``Args:`` section —
        a parameter is described only via
        ``Annotated[T, ToolParameterOptions(description=...)]``, so every
        annotation is rewrapped here, skipping ``context`` (must stay exactly
        ``ToolContext``). ``extra_doc`` and ``param_overrides`` only append to
        master text, so a master model edit keeps propagating.
        """
        func.__doc__ = get_tool_description(func.__name__).rstrip() + extra_doc

        model = resolve_tool_model(func.__name__)
        if model is None:
            return

        for param_name, param in inspect.signature(func).parameters.items():
            if param_name == "context":
                continue
            field = model.model_fields.get(param_name)
            if field is None or not field.description:
                continue
            description = field.description
            if choices := _literal_choices(field.annotation):
                description = description.rstrip() + f" One of: {', '.join(choices)}."
            if param_overrides and param_name in param_overrides:
                description = description.rstrip() + param_overrides[param_name]
            func.__annotations__[param_name] = Annotated[
                param.annotation, ToolParameterOptions(description=description)
            ]

    def guard_failures(
        func: Callable[..., Any], failure: str, mention_hints: bool
    ) -> Callable[..., Any]:
        """Wrap *func* with the logging and failure handling every tool shares.

        ``failure`` completes ``"Error {failure}: {exc}"`` and may template the
        call's own arguments (e.g. ``"adding participant '{identifier}'"``);
        ``mention_hints`` appends the room's available handles to a
        mention-related failure. ``functools.wraps`` is load-bearing: Parlant
        introspects ``__wrapped__``, so the registered signature is *func*'s own.
        """

        @functools.wraps(func)
        async def run(context: Any, *args: Any, **kwargs: Any) -> Any:
            # bind() gets its own try: a signature/argument-shape mismatch
            # raises TypeError here, before the call-handling try below even
            # starts, and there's no `call.arguments` yet to build the usual
            # failure message from.
            try:
                call = inspect.signature(func).bind(context, *args, **kwargs)
                call.apply_defaults()
            except TypeError as exc:
                logger.error(
                    "%s %s: malformed call arguments: %s",
                    LOG_PREFIX,
                    func.__name__,
                    exc,
                    exc_info=True,
                )
                return ToolResult(data=f"Error calling {func.__name__}: {exc}")

            logger.info(
                "%s %s called: session=%s%s",
                LOG_PREFIX,
                func.__name__,
                context.session_id,
                _logged_arguments(call),
            )
            try:
                result = await func(context, *args, **kwargs)
            except NoSessionTools:
                logger.error(
                    "%s %s: no tools available for session %s",
                    LOG_PREFIX,
                    func.__name__,
                    context.session_id,
                )
                return ToolResult(data=NO_SESSION_TOOLS_ERROR)
            except Exception as exc:
                context_phrase = failure.format(**call.arguments)
                logger.error(
                    "%s Error %s: %s", LOG_PREFIX, context_phrase, exc, exc_info=True
                )
                message = str(exc)
                if mention_hints and isinstance(exc, (ValueError, BandToolError)):
                    session_tools = get_session_tools(context.session_id)
                    if session_tools:
                        message = with_mention_handles(message, session_tools)
                return ToolResult(data=f"Error {context_phrase}: {message}")
            logger.info("%s %s -> %s", LOG_PREFIX, func.__name__, result)
            return result

        return run

    def band_tool(
        failure: str,
        extra_doc: str = "",
        param_overrides: dict[str, str] | None = None,
        mention_hints: bool = False,
    ) -> Callable[[Callable[..., Any]], Any]:
        """Decorator: describe the tool from the master model, guard it, register it."""

        def decorator(func: Callable[..., Any]) -> Any:
            describe_from_master(func, extra_doc, param_overrides)
            return p.tool(guard_failures(func, failure, mention_hints))

        return decorator

    def chat_tools() -> list[Any]:
        """The room tools every Parlant agent gets, capability-free."""

        @band_tool(
            "sending message",
            SEND_MESSAGE_MENTIONS_NOTE,
            param_overrides={"mentions": SEND_MESSAGE_MENTIONS_PARAM_NOTE},
            mention_hints=True,
        )
        async def band_send_message(
            context: ToolContext,
            content: str,
            mentions: str,
        ) -> ToolResult:
            tools = require_session_tools(context)
            recipients = split_mentions(mentions)
            if not recipients:
                return ToolResult(
                    data="Error: "
                    + with_mention_handles("At least one mention is required", tools)
                )

            await tools.send_message(content, recipients)
            # Tells the adapter its own reply would duplicate this one.
            mark_message_sent(context.session_id)
            return ToolResult(data=f"Message sent to {', '.join(recipients)}")

        @band_tool("sending event")
        async def band_send_event(
            context: ToolContext,
            content: str,
            message_type: str,
        ) -> ToolResult:
            tools = require_session_tools(context)
            if message_type not in ("thought", "error", "task"):
                return ToolResult(
                    data=f"Error: Invalid message_type '{message_type}'. Use 'thought', 'error', or 'task'"
                )

            await tools.send_event(content, message_type, None)
            return ToolResult(data=f"Event ({message_type}) sent successfully")

        @band_tool("adding participant '{identifier}'")
        async def band_add_participant(
            context: ToolContext,
            identifier: str,
        ) -> ToolResult:
            tools = require_session_tools(context)
            result = await tools.add_participant(identifier, "member")
            if result.get("status", "added") == "already_in_room":
                return ToolResult(
                    data=f"'{identifier}' is already in the room - no action needed"
                )
            return ToolResult(data=f"Successfully added '{identifier}' to the room")

        @band_tool("removing participant '{identifier}'")
        async def band_remove_participant(
            context: ToolContext,
            identifier: str,
        ) -> ToolResult:
            tools = require_session_tools(context)
            await tools.remove_participant(identifier)
            return ToolResult(data=f"Successfully removed '{identifier}' from the room")

        @band_tool("looking up peers", LOOKUP_PEERS_RETURN_NOTE)
        async def band_lookup_peers(
            context: ToolContext,
        ) -> ToolResult:
            tools = require_session_tools(context)
            # Pagination is rarely needed for agent lookups, so it isn't exposed.
            data = serialize_tool_result(await tools.lookup_peers(page=1, page_size=50))
            peers = data.get("data") or []
            if not peers:
                return ToolResult(data="No available agents found")

            metadata = data.get("metadata") or {}
            lines = [
                f"Available agents (page {metadata.get('page', 1)} of "
                f"{metadata.get('total_pages', 1)}):"
            ]
            lines.extend(
                f"- {peer.get('name', 'Unknown')} ({peer.get('type', 'Agent')}): "
                f"{peer.get('description') or 'No description'}"
                for peer in peers
            )
            return ToolResult(data="\n".join(lines))

        @band_tool("getting participants")
        async def band_get_participants(
            context: ToolContext,
        ) -> ToolResult:
            tools = require_session_tools(context)
            result = await tools.get_participants()
            if not isinstance(result, list):
                return ToolResult(data=str(result))

            participants = serialize_tool_result(result)
            if not participants:
                return ToolResult(data="No participants in the room")
            lines = ["Current participants:"]
            lines.extend(
                f"- {participant.get('name', 'Unknown')} "
                f"({participant.get('type', 'Unknown')})"
                for participant in participants
            )
            return ToolResult(data="\n".join(lines))

        @band_tool("creating chatroom")
        async def band_create_chatroom(
            context: ToolContext,
            task_id: str = "",
        ) -> ToolResult:
            tools = require_session_tools(context)
            room_id = await tools.create_chatroom(or_none(task_id))
            return ToolResult(data=f"Created new chat room: {room_id}")

        return [
            band_send_message,
            band_send_event,
            band_add_participant,
            band_remove_participant,
            band_lookup_peers,
            band_get_participants,
            band_create_chatroom,
        ]

    def contact_tools() -> list[Any]:
        """Contact management — gated behind ``Capability.CONTACTS``."""

        @band_tool("listing contacts")
        async def band_list_contacts(
            context: ToolContext,
            page: int = 1,
            page_size: int = 50,
        ) -> ToolResult:
            tools = require_session_tools(context)
            data = serialize_tool_result(await tools.list_contacts(page, page_size))
            return ToolResult(data=json.dumps(data, default=str))

        @band_tool("adding contact")
        async def band_add_contact(
            context: ToolContext,
            handle: str,
            message: str = "",
        ) -> ToolResult:
            tools = require_session_tools(context)
            data = serialize_tool_result(
                await tools.add_contact(handle, or_none(message))
            )
            status = (
                data.get("status", "pending") if isinstance(data, dict) else "pending"
            )
            return ToolResult(data=f"Contact request to {handle}: {status}")

        @band_tool("removing contact")
        async def band_remove_contact(
            context: ToolContext,
            handle: str = "",
            contact_id: str = "",
        ) -> ToolResult:
            tools = require_session_tools(context)
            if not handle and not contact_id:
                return ToolResult(
                    data="Error: Either handle or contact_id must be provided"
                )

            await tools.remove_contact(or_none(handle), or_none(contact_id))
            return ToolResult(
                data=f"Contact '{handle or contact_id}' removed successfully"
            )

        @band_tool("listing contact requests")
        async def band_list_contact_requests(
            context: ToolContext,
            page: int = 1,
            page_size: int = 50,
            sent_status: str = "pending",
        ) -> ToolResult:
            tools = require_session_tools(context)
            data = serialize_tool_result(
                await tools.list_contact_requests(page, page_size, sent_status)
            )
            return ToolResult(data=json.dumps(data, default=str))

        @band_tool("responding to contact request")
        async def band_respond_contact_request(
            context: ToolContext,
            action: str,
            handle: str = "",
            request_id: str = "",
        ) -> ToolResult:
            tools = require_session_tools(context)
            if not handle and not request_id:
                return ToolResult(
                    data="Error: Either handle or request_id must be provided"
                )
            if action not in ("approve", "reject", "cancel"):
                return ToolResult(
                    data=f"Error: Invalid action '{action}'. Use 'approve', 'reject', or 'cancel'"
                )

            data = serialize_tool_result(
                await tools.respond_contact_request(
                    action, or_none(handle), or_none(request_id)
                )
            )
            status = data.get("status", action) if isinstance(data, dict) else action
            return ToolResult(data=f"Contact request {action}d: {status}")

        return [
            band_list_contacts,
            band_add_contact,
            band_remove_contact,
            band_list_contact_requests,
            band_respond_contact_request,
        ]

    def file_tools() -> list[Any]:
        """Room files — gated behind ``Capability.FILES``."""

        @band_tool("listing room files")
        async def band_list_room_files(
            context: ToolContext,
            cursor: str = "",
        ) -> ToolResult:
            tools = require_session_tools(context)
            data = serialize_tool_result(await tools.list_room_files(or_none(cursor)))
            files = data.get("data") or []
            if not files:
                return ToolResult(data="No files found in this room")

            lines = ["Files in this room:"]
            lines.extend(
                f"- {file.get('name', 'Unknown')} "
                f"({file.get('content_type', 'unknown')}, {file.get('bytes', 0)} bytes) "
                f"id={file.get('id', '')}"
                for file in files
            )
            if next_cursor := data.get("next_cursor"):
                lines.append(
                    f"More files available; call again with cursor='{next_cursor}' "
                    "to see the rest."
                )
            return ToolResult(data="\n".join(lines))

        @band_tool("reading room file")
        async def band_read_room_file(
            context: ToolContext,
            file_id: str,
        ) -> ToolResult:
            tools = require_session_tools(context)
            result = await tools.read_room_file(file_id)
            match result:
                case {"text": str() as text}:
                    note = result.get("description")
                    return ToolResult(data=f"{text}\n\n({note})" if note else text)
                case _ if is_mcp_content_result(result):
                    # A Parlant ToolResult has no multimodal channel, so the
                    # image is described rather than passed through as vision.
                    mime_type = result["content"][0].get("mimeType", "image")
                    return ToolResult(
                        data=(
                            f"This file is a {mime_type} image; image content cannot "
                            "be shown inline by this tool."
                        )
                    )

            summary = (
                f"{result.get('name', 'Unknown')} "
                f"({result.get('content_type', 'unknown')}, "
                f"{result.get('bytes', 0)} bytes)"
            )
            description = result.get("description", "")
            return ToolResult(
                data=f"{summary}. {description}" if description else summary
            )

        @band_tool(
            "sending room file",
            SEND_MESSAGE_MENTIONS_NOTE,
            param_overrides={"mentions": SEND_MESSAGE_MENTIONS_PARAM_NOTE},
            mention_hints=True,
        )
        async def band_send_room_file(
            context: ToolContext,
            content: str,
            filename: str,
            mentions: str,
            caption: str = "",
        ) -> ToolResult:
            tools = require_session_tools(context)
            recipients = split_mentions(mentions)
            if not recipients:
                return ToolResult(
                    data="Error: "
                    + with_mention_handles("At least one mention is required", tools)
                )

            result = await tools.send_room_file(content, filename, caption, recipients)
            attachment = result.get("attachment") or {}
            return ToolResult(
                data=f"Uploaded '{attachment.get('name', filename)}' "
                f"(id={attachment.get('id', '')}) and shared with "
                f"{', '.join(recipients)}"
            )

        return [
            band_list_room_files,
            band_read_room_file,
            band_send_room_file,
        ]

    def task_tools() -> list[Any]:
        """Task board — gated behind ``Capability.TASKS``."""

        @band_tool("listing tasks")
        async def band_list_tasks(
            context: ToolContext,
            state: TaskListState | None = None,
            cursor: str | None = None,
            limit: int | None = None,
        ) -> ToolResult:
            tools = require_session_tools(context)
            data = serialize_tool_result(
                await tools.list_tasks(state=state, cursor=cursor, limit=limit)
            )
            return ToolResult(data=json.dumps(data, default=str))

        @band_tool("creating task '{subject}'")
        async def band_create_task(
            context: ToolContext,
            subject: str,
            detail: str | None = None,
            supersedes_id: str | None = None,
        ) -> ToolResult:
            tools = require_session_tools(context)
            data = serialize_tool_result(
                await tools.create_task(
                    subject, detail=detail, supersedes_id=supersedes_id
                )
            )
            return ToolResult(data=json.dumps(data, default=str))

        @band_tool("getting task '{id}'")
        async def band_get_task(
            context: ToolContext,
            id: str,
            include: str | None = None,
        ) -> ToolResult:
            tools = require_session_tools(context)
            data = serialize_tool_result(
                await tools.get_task(
                    id, include=cast(Literal["history"] | None, include)
                )
            )
            return ToolResult(data=json.dumps(data, default=str))

        @band_tool("updating task '{id}'")
        async def band_update_task(
            context: ToolContext,
            id: str,
            status: TaskAssignmentStatus | None = None,
            active_form: str | None = None,
            comment: str | None = None,
            subject: str | None = None,
            detail: str | None = None,
            state: TaskLifecycleState | None = None,
        ) -> ToolResult:
            tools = require_session_tools(context)
            data = serialize_tool_result(
                await tools.update_task(
                    id,
                    status=status,
                    active_form=active_form,
                    comment=comment,
                    subject=subject,
                    detail=detail,
                    state=state,
                )
            )
            return ToolResult(data=json.dumps(data, default=str))

        @band_tool("getting task history for '{id}'")
        async def band_get_task_history(
            context: ToolContext,
            id: str,
            cursor: str | None = None,
            limit: int | None = None,
        ) -> ToolResult:
            tools = require_session_tools(context)
            data = serialize_tool_result(
                await tools.get_task_history(id, cursor=cursor, limit=limit)
            )
            return ToolResult(data=json.dumps(data, default=str))

        @band_tool("getting board")
        async def band_get_board(
            context: ToolContext,
            include: str | None = None,
        ) -> ToolResult:
            tools = require_session_tools(context)
            data = serialize_tool_result(
                await tools.get_board(include=cast(Literal["history"] | None, include))
            )
            return ToolResult(data=json.dumps(data, default=str))

        @band_tool("setting board")
        async def band_set_board(
            context: ToolContext,
            goal_title: str | None = None,
            goal_summary: str | None = None,
        ) -> ToolResult:
            tools = require_session_tools(context)
            data = serialize_tool_result(
                await tools.set_board(goal_title=goal_title, goal_summary=goal_summary)
            )
            return ToolResult(data=json.dumps(data, default=str))

        return [
            band_list_tasks,
            band_create_task,
            band_get_task,
            band_update_task,
            band_get_task_history,
            band_get_board,
            band_set_board,
        ]

    capabilities = features.capabilities if features else None
    tools = chat_tools()
    if capabilities is None or Capability.CONTACTS in capabilities:
        tools += contact_tools()
    if capabilities is None or Capability.FILES in capabilities:
        tools += file_tools()
    if capabilities is None or Capability.TASKS in capabilities:
        tools += task_tools()
    return tools
