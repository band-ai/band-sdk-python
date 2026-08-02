"""Types for ACP server adapter."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from band.core.types import ToolEventKey


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

    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class ACPToolCall:
    """One ACP tool invocation, normalized for room persistence."""

    tool_call_id: str
    name: str
    arguments: dict[str, Any]

    def room_event(self) -> dict[ToolEventKey, object]:
        """Return the canonical Band tool-call payload."""
        return {
            ToolEventKey.NAME: self.name,
            ToolEventKey.ARGS: self.arguments,
            ToolEventKey.TOOL_CALL_ID: self.tool_call_id,
        }


@dataclass
class ACPToolResult:
    """The finalized outcome of an :class:`ACPToolCall`."""

    call: ACPToolCall
    output: str
    status: ToolStatus | str | None

    @property
    def is_error(self) -> bool:
        return self.status == ToolStatus.FAILED

    def room_event(self) -> dict[ToolEventKey, object]:
        """Return the canonical Band tool-result payload."""
        return {
            ToolEventKey.NAME: self.call.name,
            ToolEventKey.OUTPUT: self.output,
            ToolEventKey.TOOL_CALL_ID: self.call.tool_call_id,
            ToolEventKey.IS_ERROR: self.is_error,
        }


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
        done_event: Signals when the prompt has been fully answered.
        terminal_message_seen: Tracks whether a terminal room message has arrived.
        completion_task: Debounced completion task for multi-message replies.
    """

    session_id: str
    done_event: asyncio.Event = field(default_factory=asyncio.Event)
    terminal_message_seen: bool = False
    completion_task: asyncio.Task[None] | None = None
