"""Types for ACP server adapter."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, cast

from band_sdk_core import AgentFailure
from pydantic import BaseModel, ConfigDict, JsonValue

from band.runtime.tools import mcp_tool_spelling


class ACPStopReason(StrEnum):
    """Terminal stop reasons the Band ACP server can return."""

    END_TURN = "end_turn"
    CANCELLED = "cancelled"


class ACPServerUpdate(StrEnum):
    """ACP session update kinds constructed directly by this server."""

    AGENT_MESSAGE_CHUNK = "agent_message_chunk"


PromptOutcome = ACPStopReason | AgentFailure


class ConcurrentPromptError(ValueError):
    """A room already has an active ACP prompt."""


class ToolCallRoomEvent(BaseModel):
    """Canonical room payload for a tool-call event."""

    model_config = ConfigDict(frozen=True)

    name: str
    args: dict[str, JsonValue]
    tool_call_id: str


class ToolResultRoomEvent(BaseModel):
    """Canonical room payload for a tool-result event."""

    model_config = ConfigDict(frozen=True)

    name: str
    output: str
    tool_call_id: str
    is_error: bool


class ChunkType(StrEnum):
    """The kind of a parsed ACP session-update chunk.

    Single source of truth for the chunk-type vocabulary shared between the
    producers that parse ACP session updates (``client_runtime``,
    ``client_profiles``) and the consumers that emit them to a Band room
    (``room_emitter``). ``StrEnum`` members are ``str``, so a member compares
    equal to its literal and serializes as it — reference these instead of the
    bare strings.
    """

    TEXT = "text"
    THOUGHT = "thought"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    PLAN = "plan"


class ToolStatus(StrEnum):
    """ACP tool-call lifecycle status, as reported on tool_call updates.

    Single source of truth for the status values the runtime records on a
    tool_call/tool_result chunk and the consumers compare against.
    """

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class ACPToolCall:
    """One ACP tool invocation, normalized for room persistence."""

    tool_call_id: str
    name: str
    arguments: dict[str, JsonValue]

    @classmethod
    def from_acp(
        cls,
        tool_call: object,
        canonicalize: Callable[[str], str] | None = None,
    ) -> ACPToolCall:
        """Normalize an ACP tool-call model into the room lifecycle shape.

        ``canonicalize`` rewrites the runtime's MCP spelling of the tool name
        (e.g. Copilot's ``band-band_send_message``) at construction, so the
        canonical name is the only one the object ever carries.
        """
        raw_input = getattr(tool_call, "raw_input", None)
        name, arguments = _mcp_invocation(raw_input) or (
            str(
                getattr(tool_call, "title", None)
                or getattr(tool_call, "name", "unknown")
            ),
            cast(dict[str, JsonValue], raw_input)
            if isinstance(raw_input, dict)
            else {},
        )
        if canonicalize is not None:
            name = canonicalize(name)
        return cls(
            tool_call_id=str(getattr(tool_call, "tool_call_id", "")),
            name=name,
            arguments=arguments,
        )

    def room_event(self) -> ToolCallRoomEvent:
        """Return the canonical Band tool-call payload."""
        return ToolCallRoomEvent(
            name=self.name,
            args=self.arguments,
            tool_call_id=self.tool_call_id,
        )


# The ``rawInput`` keys each runtime reports an MCP call under, as (server,
# tool, arguments): codex-acp, then Cursor.
_MCP_INVOCATION_KEYS: tuple[tuple[str, str, str], ...] = (
    ("server", "tool", "arguments"),
    ("providerIdentifier", "toolName", "args"),
)


def _mcp_invocation(
    raw_input: object,
) -> tuple[str, dict[str, JsonValue]] | None:
    """Name and arguments of an MCP call reported in ``rawInput`` (see
    ``_MCP_INVOCATION_KEYS``), whose title is only a display string."""
    if not isinstance(raw_input, dict):
        return None
    keys = next(
        (keys for keys in _MCP_INVOCATION_KEYS if raw_input.keys() == set(keys)),
        None,
    )
    if keys is None:
        return None
    server, tool, arguments = (raw_input[key] for key in keys)
    if not isinstance(server, str) or not isinstance(tool, str):
        return None
    args = cast(dict[str, JsonValue], arguments) if isinstance(arguments, dict) else {}
    return mcp_tool_spelling(server, tool), args


@dataclass
class ACPToolResult:
    """The finalized outcome of an :class:`ACPToolCall`."""

    call: ACPToolCall
    output: str
    status: ToolStatus | str | None

    @property
    def is_error(self) -> bool:
        return self.status == ToolStatus.FAILED

    def room_event(self) -> ToolResultRoomEvent:
        """Return the canonical Band tool-result payload."""
        return ToolResultRoomEvent(
            name=self.call.name,
            output=self.output,
            tool_call_id=self.call.tool_call_id,
            is_error=self.is_error,
        )


@dataclass
class CollectedChunk:
    """A parsed chunk from an ACP session_update.

    Used by BandACPClient to buffer rich response chunks
    (text, thoughts, tool calls, tool results, plans) from
    remote ACP agents.

    Attributes:
        chunk_type: The kind of chunk — a ``ChunkType`` value.
        content: The text content of the chunk.
        metadata: Additional metadata (e.g., tool_call_id, status).
        from_raw: For tool_result chunks, True when ``content`` is a stringified
            ``rawOutput`` fallback rather than readable content-block text. Lets
            the collapse of repeated tool_call_updates prefer the readable frame
            (see ``ACPCollectingClient._fold_result``); ignored otherwise.
        echo: For tool_result chunks, the ``rawOutput.structuredContent``
            payload that was proven appended to (and stripped from) this
            result's content (see ``_unwrap_structured_result``); ``None`` when
            the content was taken as-is. Recording the proven payload -- not
            just that cleanup occurred -- lets folding recognize a later frame
            that re-reports exactly that duplicate (see
            ``ACPCollectingClient._fold_result``) without guessing at
            encodings; ignored otherwise.
        tool: The normalized tool call or result for tool chunks. This is the
            authoritative lifecycle data used for room persistence; ``content``
            remains the readable narration for ACP-local consumers.
    """

    chunk_type: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    from_raw: bool = False
    echo: dict[str, Any] | None = None
    tool: ACPToolCall | ACPToolResult | None = None


@dataclass
class ACPSessionState:
    """Session state extracted from platform history.

    Used by ACPServerHistoryConverter to restore ACP server session state
    when the agent rejoins a chat room.

    Attributes:
        session_to_room: Mapping of ACP session_id to Band room_id.
        session_cwd: Mapping of ACP session_id to editor working directory.
        session_mcp_servers: Mapping of ACP session_id to editor MCP servers.
    """

    session_to_room: dict[str, str] = field(default_factory=dict)
    session_cwd: dict[str, str] = field(default_factory=dict)
    session_mcp_servers: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


@dataclass
class PendingACPPrompt:
    """Tracks an in-flight ACP prompt awaiting Band response.

    When the ACP server receives a prompt from the editor, it creates a
    PendingACPPrompt to correlate the eventual response from the Band
    platform with the ACP session_update back to the editor.

    Attributes:
        session_id: The ACP session identifier.
        done_event: Signals when the prompt has ended.
        outcome: The first terminal result, once settled.
        completion_task: Debounced completion task for multi-message replies.
        posted: True once the room accepted this prompt's post.
        reply_started: True once a text reply has opened the grace window.
    """

    session_id: str
    done_event: asyncio.Event = field(default_factory=asyncio.Event)
    outcome: PromptOutcome | None = None
    completion_task: asyncio.Task[None] | None = None
    posted: bool = False
    reply_started: bool = False

    def finish(self, outcome: PromptOutcome | None) -> None:
        """Stop the grace timer and mark the prompt as ended."""
        completion_task = self.completion_task
        self.completion_task = None
        if (
            completion_task is not None
            and completion_task is not asyncio.current_task()
        ):
            completion_task.cancel()
        if outcome is not None:
            self.outcome = outcome
        self.done_event.set()
