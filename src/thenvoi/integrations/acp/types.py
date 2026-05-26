"""Types for ACP server adapter."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CollectedChunk:
    """A parsed chunk from an ACP session_update.

    Used by ThenvoiACPClient to buffer rich response chunks
    (text, thoughts, tool calls, tool results, plans) from
    remote ACP agents.

    Attributes:
        chunk_type: The type of chunk ("text", "thought", "tool_call",
            "tool_result", "plan").
        content: The text content of the chunk.
        metadata: Additional metadata (e.g., tool_call_id, status).
    """

    chunk_type: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ACPSessionState:
    """Session state extracted from platform history.

    Used by ACPServerHistoryConverter to restore ACP server session state
    when the agent rejoins a chat room.

    Attributes:
        session_to_room: Mapping of ACP session_id to Thenvoi room_id.
        session_cwd: Mapping of ACP session_id to editor working directory.
        session_mcp_servers: Mapping of ACP session_id to editor MCP servers.
    """

    session_to_room: dict[str, str] = field(default_factory=dict)
    session_cwd: dict[str, str] = field(default_factory=dict)
    session_mcp_servers: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


@dataclass
class PendingACPPrompt:
    """Tracks an in-flight ACP prompt awaiting Thenvoi response.

    When the ACP server receives a prompt from the editor, it creates a
    PendingACPPrompt to correlate the eventual response from the Thenvoi
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
