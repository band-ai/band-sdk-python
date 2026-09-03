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

NOTE: We intentionally do NOT use `from __future__ import annotations` here
because Parlant's @p.tool decorator checks annotation types at runtime.
"""

import inspect
import json
import logging
import warnings
from typing import Annotated, Any, Callable, Literal, Optional, get_args, get_origin

from band.core.exceptions import BandToolError
from band.core.types import AdapterFeatures, Capability
from band.runtime.tools import (
    append_available_mention_handles,
    get_tool_description,
    resolve_tool_model,
    serialize_tool_result,
)

logger = logging.getLogger(__name__)

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
        import parlant.sdk as p  # type: ignore[missing-import]  # noqa: PLC0415
        from parlant.core.tools import (  # type: ignore[missing-import]  # noqa: PLC0415
            ToolContext,
            ToolParameterOptions,
            ToolResult,
        )
    except ImportError:
        logger.warning("Parlant SDK not installed, skipping tool creation")
        return []

    def band_tool(
        extra_doc: str = "",
        param_overrides: dict[str, str] | None = None,
    ) -> Callable[[Callable[..., Any]], Any]:
        """Decorator: describe *func* and its parameters from the master model, then register it.

        The tool name is never retyped as a string — it's ``func.__name__``,
        which is always written to match its ``TOOL_MODELS`` entry (e.g. the
        function below is literally named ``band_send_message``). ``extra_doc``
        appends prose the master tool description can't express (a
        Parlant-only argument shape); ``param_overrides`` does the same per
        argument, keyed by parameter name. Neither ever replaces master text —
        only appends — so a master model edit keeps propagating.

        Parlant's own schema builder never reads a docstring's ``Args:``
        section (unlike pydantic-ai's griffe parser) — a parameter only gets a
        description if its type annotation is
        ``Annotated[T, ToolParameterOptions(description=...)]``. So this also
        wraps each parameter's annotation from the master model's
        ``Field(description=...)`` before registering, skipping ``context``
        (must stay exactly ``ToolContext``) and any parameter with no master
        description. A master field typed ``Literal[...]`` has its string
        choices folded into that same description text (see
        ``_literal_choices``) — the function keeps its own ``str``
        annotation, since handing Parlant the ``Literal`` itself crashes
        registration.
        """

        def decorator(func: Callable[..., Any]) -> Any:
            func.__doc__ = get_tool_description(func.__name__).rstrip() + extra_doc

            model = resolve_tool_model(func.__name__)
            if model is not None:
                for param_name, param in inspect.signature(func).parameters.items():
                    if param_name == "context":
                        continue
                    field = model.model_fields.get(param_name)
                    if field is None or not field.description:
                        continue
                    description = field.description
                    if choices := _literal_choices(field.annotation):
                        description = (
                            description.rstrip() + f" One of: {', '.join(choices)}."
                        )
                    if param_overrides and param_name in param_overrides:
                        description = description.rstrip() + param_overrides[param_name]
                    func.__annotations__[param_name] = Annotated[
                        param.annotation, ToolParameterOptions(description=description)
                    ]

            return p.tool(func)

        return decorator

    @band_tool(
        SEND_MESSAGE_MENTIONS_NOTE,
        param_overrides={"mentions": SEND_MESSAGE_MENTIONS_PARAM_NOTE},
    )
    async def band_send_message(
        context: ToolContext,
        content: str,
        mentions: str,
    ) -> ToolResult:
        logger.info(
            "[Parlant Tool] send_message called: session=%s, content=%s..., mentions=%s",
            context.session_id,
            content[:50],
            mentions,
        )
        tools = get_session_tools(context.session_id)
        if not tools:
            logger.error(
                "[Parlant Tool] send_message: No tools available for session %s",
                context.session_id,
            )
            return ToolResult(data="Error: No tools available in current context")

        try:
            # Parse mentions from comma-separated string
            mention_list = [m.strip() for m in mentions.split(",") if m.strip()]
            if not mention_list:
                logger.warning("[Parlant Tool] send_message: No mentions provided")
                error = append_available_mention_handles(
                    "At least one mention is required",
                    tools.participants,
                    getattr(tools, "agent_id", None),
                )
                return ToolResult(data=f"Error: {error}")

            logger.info("[Parlant Tool] Sending message to: %s", mention_list)
            await tools.send_message(content, mention_list)
            # Mark that we sent a message via the tool (so adapter doesn't duplicate)
            mark_message_sent(context.session_id)
            logger.info("[Parlant Tool] Message sent successfully via tool")
            return ToolResult(data=f"Message sent to {', '.join(mention_list)}")
        except Exception as e:
            logger.error("[Parlant Tool] Error sending message: %s", e, exc_info=True)
            error = str(e)
            if isinstance(e, (ValueError, BandToolError)):
                error = append_available_mention_handles(
                    error,
                    tools.participants,
                    getattr(tools, "agent_id", None),
                )
            return ToolResult(data=f"Error sending message: {error}")

    @band_tool()
    async def band_send_event(
        context: ToolContext,
        content: str,
        message_type: str,
    ) -> ToolResult:
        logger.info(
            "[Parlant Tool] send_event called: session=%s, type=%s",
            context.session_id,
            message_type,
        )
        tools = get_session_tools(context.session_id)
        if not tools:
            logger.error(
                "[Parlant Tool] send_event: No tools available for session %s",
                context.session_id,
            )
            return ToolResult(data="Error: No tools available in current context")

        if message_type not in ("thought", "error", "task"):
            return ToolResult(
                data=f"Error: Invalid message_type '{message_type}'. Use 'thought', 'error', or 'task'"
            )

        try:
            await tools.send_event(content, message_type, None)
            logger.info("[Parlant Tool] Event (%s) sent successfully", message_type)
            return ToolResult(data=f"Event ({message_type}) sent successfully")
        except Exception as e:
            logger.error("[Parlant Tool] Error sending event: %s", e, exc_info=True)
            return ToolResult(data=f"Error sending event: {e}")

    @band_tool()
    async def band_add_participant(
        context: ToolContext,
        identifier: str,
    ) -> ToolResult:
        logger.info(
            "[Parlant Tool] add_participant called: session=%s, identifier=%s",
            context.session_id,
            identifier,
        )
        tools = get_session_tools(context.session_id)
        if not tools:
            logger.error(
                "[Parlant Tool] add_participant: No tools available for session %s",
                context.session_id,
            )
            return ToolResult(data="Error: No tools available in current context")

        try:
            result = await tools.add_participant(identifier, "member")
            status = result.get("status", "added")
            if status == "already_in_room":
                logger.info("[Parlant Tool] '%s' is already in the room", identifier)
                return ToolResult(
                    data=f"'{identifier}' is already in the room - no action needed"
                )
            logger.info(
                "[Parlant Tool] Successfully added '%s' to the room", identifier
            )
            return ToolResult(data=f"Successfully added '{identifier}' to the room")
        except Exception as e:
            logger.error(
                "[Parlant Tool] Error adding participant '%s': %s",
                identifier,
                e,
                exc_info=True,
            )
            return ToolResult(data=f"Error adding participant '{identifier}': {e}")

    @band_tool()
    async def band_remove_participant(
        context: ToolContext,
        identifier: str,
    ) -> ToolResult:
        logger.info(
            "[Parlant Tool] remove_participant called: session=%s, identifier=%s",
            context.session_id,
            identifier,
        )
        tools = get_session_tools(context.session_id)
        if not tools:
            logger.error(
                "[Parlant Tool] remove_participant: No tools available for session %s",
                context.session_id,
            )
            return ToolResult(data="Error: No tools available in current context")

        try:
            await tools.remove_participant(identifier)
            logger.info(
                "[Parlant Tool] Successfully removed '%s' from the room", identifier
            )
            return ToolResult(data=f"Successfully removed '{identifier}' from the room")
        except Exception as e:
            logger.error(
                "[Parlant Tool] Error removing participant '%s': %s",
                identifier,
                e,
                exc_info=True,
            )
            return ToolResult(data=f"Error removing participant '{identifier}': {e}")

    @band_tool(LOOKUP_PEERS_RETURN_NOTE)
    async def band_lookup_peers(
        context: ToolContext,
    ) -> ToolResult:
        logger.info(
            "[Parlant Tool] lookup_peers called: session=%s", context.session_id
        )
        tools = get_session_tools(context.session_id)
        if not tools:
            logger.error(
                "[Parlant Tool] lookup_peers: No tools available for session %s",
                context.session_id,
            )
            return ToolResult(data="Error: No tools available in current context")

        try:
            # Use defaults - pagination rarely needed for agent lookups
            result = await tools.lookup_peers(page=1, page_size=50)
            logger.info("[Parlant Tool] lookup_peers result: %s", result)
            # Normalize Fern model -> dict for uniform handling
            data = serialize_tool_result(result)
            peers = data.get("data") or []
            metadata = data.get("metadata") or {}
            if not peers:
                return ToolResult(data="No available agents found")

            page_num = metadata.get("page", 1)
            total_pages = metadata.get("total_pages", 1)
            lines = [f"Available agents (page {page_num} of {total_pages}):"]
            for peer in peers:
                name = peer.get("name", "Unknown")
                desc = peer.get("description") or "No description"
                peer_type = peer.get("type", "Agent")
                lines.append(f"- {name} ({peer_type}): {desc}")
            return ToolResult(data="\n".join(lines))
        except Exception as e:
            logger.error("[Parlant Tool] Error looking up peers: %s", e, exc_info=True)
            return ToolResult(data=f"Error looking up peers: {e}")

    @band_tool()
    async def band_get_participants(
        context: ToolContext,
    ) -> ToolResult:
        logger.info(
            "[Parlant Tool] get_participants called: session=%s", context.session_id
        )
        tools = get_session_tools(context.session_id)
        if not tools:
            logger.error(
                "[Parlant Tool] get_participants: No tools available for session %s",
                context.session_id,
            )
            return ToolResult(data="Error: No tools available in current context")

        try:
            result = await tools.get_participants()
            logger.info("[Parlant Tool] get_participants result: %s", result)
            # Normalize Fern models -> dicts for uniform handling
            if isinstance(result, list):
                items = serialize_tool_result(result)
                if not items:
                    return ToolResult(data="No participants in the room")
                lines = ["Current participants:"]
                for participant in items:
                    name = participant.get("name", "Unknown")
                    p_type = participant.get("type", "Unknown")
                    lines.append(f"- {name} ({p_type})")
                return ToolResult(data="\n".join(lines))
            return ToolResult(data=str(result))
        except Exception as e:
            logger.error(
                "[Parlant Tool] Error getting participants: %s", e, exc_info=True
            )
            return ToolResult(data=f"Error getting participants: {e}")

    @band_tool()
    async def band_create_chatroom(
        context: ToolContext,
        task_id: str = "",
    ) -> ToolResult:
        logger.info(
            "[Parlant Tool] create_chatroom called: session=%s, task_id=%s",
            context.session_id,
            task_id,
        )
        tools = get_session_tools(context.session_id)
        if not tools:
            logger.error(
                "[Parlant Tool] create_chatroom: No tools available for session %s",
                context.session_id,
            )
            return ToolResult(data="Error: No tools available in current context")

        try:
            result = await tools.create_chatroom(task_id if task_id else None)
            logger.info("[Parlant Tool] Created chatroom: %s", result)
            return ToolResult(data=f"Created new chat room: {result}")
        except Exception as e:
            logger.error("[Parlant Tool] Error creating chatroom: %s", e, exc_info=True)
            return ToolResult(data=f"Error creating chatroom: {e}")

    include_contacts = features is None or Capability.CONTACTS in features.capabilities

    @band_tool()
    async def band_list_contacts(
        context: ToolContext,
        page: int = 1,
        page_size: int = 50,
    ) -> ToolResult:
        logger.info(
            "[Parlant Tool] list_contacts called: session=%s, page=%s",
            context.session_id,
            page,
        )
        tools = get_session_tools(context.session_id)
        if not tools:
            logger.error(
                "[Parlant Tool] list_contacts: No tools available for session %s",
                context.session_id,
            )
            return ToolResult(data="Error: No tools available in current context")

        try:
            result = await tools.list_contacts(page, page_size)
            # Fern model: serialize via model_dump if available, fallback to str
            data = serialize_tool_result(result)
            return ToolResult(data=json.dumps(data, default=str))
        except Exception as e:
            logger.error("[Parlant Tool] Error listing contacts: %s", e, exc_info=True)
            return ToolResult(data=f"Error listing contacts: {e}")

    @band_tool()
    async def band_add_contact(
        context: ToolContext,
        handle: str,
        message: str = "",
    ) -> ToolResult:
        logger.info(
            "[Parlant Tool] add_contact called: session=%s, handle=%s",
            context.session_id,
            handle,
        )
        tools = get_session_tools(context.session_id)
        if not tools:
            logger.error(
                "[Parlant Tool] add_contact: No tools available for session %s",
                context.session_id,
            )
            return ToolResult(data="Error: No tools available in current context")

        try:
            result = await tools.add_contact(handle, message if message else None)
            data = serialize_tool_result(result)
            status = (
                data.get("status", "pending") if isinstance(data, dict) else "pending"
            )
            return ToolResult(data=f"Contact request to {handle}: {status}")
        except Exception as e:
            logger.error("[Parlant Tool] Error adding contact: %s", e, exc_info=True)
            return ToolResult(data=f"Error adding contact: {e}")

    @band_tool()
    async def band_remove_contact(
        context: ToolContext,
        handle: str = "",
        contact_id: str = "",
    ) -> ToolResult:
        logger.info(
            "[Parlant Tool] remove_contact called: session=%s, handle=%s, contact_id=%s",
            context.session_id,
            handle,
            contact_id,
        )
        tools = get_session_tools(context.session_id)
        if not tools:
            logger.error(
                "[Parlant Tool] remove_contact: No tools available for session %s",
                context.session_id,
            )
            return ToolResult(data="Error: No tools available in current context")

        h = handle if handle else None
        cid = contact_id if contact_id else None
        if not h and not cid:
            return ToolResult(
                data="Error: Either handle or contact_id must be provided"
            )

        try:
            await tools.remove_contact(h, cid)
            identifier = handle or contact_id
            return ToolResult(data=f"Contact '{identifier}' removed successfully")
        except Exception as e:
            logger.error("[Parlant Tool] Error removing contact: %s", e, exc_info=True)
            return ToolResult(data=f"Error removing contact: {e}")

    @band_tool()
    async def band_list_contact_requests(
        context: ToolContext,
        page: int = 1,
        page_size: int = 50,
        sent_status: str = "pending",
    ) -> ToolResult:
        logger.info(
            "[Parlant Tool] list_contact_requests called: session=%s, sent_status=%s",
            context.session_id,
            sent_status,
        )
        tools = get_session_tools(context.session_id)
        if not tools:
            logger.error(
                "[Parlant Tool] list_contact_requests: No tools available for session %s",
                context.session_id,
            )
            return ToolResult(data="Error: No tools available in current context")

        try:
            result = await tools.list_contact_requests(page, page_size, sent_status)
            # Fern model: serialize via model_dump if available, fallback to str
            data = serialize_tool_result(result)
            return ToolResult(data=json.dumps(data, default=str))
        except Exception as e:
            logger.error(
                "[Parlant Tool] Error listing contact requests: %s", e, exc_info=True
            )
            return ToolResult(data=f"Error listing contact requests: {e}")

    @band_tool()
    async def band_respond_contact_request(
        context: ToolContext,
        action: str,
        handle: str = "",
        request_id: str = "",
    ) -> ToolResult:
        logger.info(
            "[Parlant Tool] respond_contact_request called: session=%s, action=%s",
            context.session_id,
            action,
        )
        tools = get_session_tools(context.session_id)
        if not tools:
            logger.error(
                "[Parlant Tool] respond_contact_request: No tools available for session %s",
                context.session_id,
            )
            return ToolResult(data="Error: No tools available in current context")

        h = handle if handle else None
        rid = request_id if request_id else None
        if not h and not rid:
            return ToolResult(
                data="Error: Either handle or request_id must be provided"
            )

        if action not in ("approve", "reject", "cancel"):
            return ToolResult(
                data=f"Error: Invalid action '{action}'. Use 'approve', 'reject', or 'cancel'"
            )

        try:
            result = await tools.respond_contact_request(action, h, rid)
            data = serialize_tool_result(result)
            status = data.get("status", action) if isinstance(data, dict) else action
            return ToolResult(data=f"Contact request {action}d: {status}")
        except Exception as e:
            logger.error(
                "[Parlant Tool] Error responding to contact request: %s",
                e,
                exc_info=True,
            )
            return ToolResult(data=f"Error responding to contact request: {e}")

    tools = [
        band_send_message,
        band_send_event,
        band_add_participant,
        band_remove_participant,
        band_lookup_peers,
        band_get_participants,
        band_create_chatroom,
    ]

    if include_contacts:
        tools.extend(
            [
                band_list_contacts,
                band_add_contact,
                band_remove_contact,
                band_list_contact_requests,
                band_respond_contact_request,
            ]
        )

    return tools
