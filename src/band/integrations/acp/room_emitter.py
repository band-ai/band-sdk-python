"""Live, causally-ordered emission of one ACP turn's output to a Band room."""

from __future__ import annotations

import logging
from typing import Any

from band.core.contracts import (
    ErrorEvent,
    PlanEvent,
    RunFailedEvent,
    TextDeltaEvent,
    ThoughtEvent,
    ToolCallEvent,
    ToolResultEvent,
    ToolStatus,
    TurnEvent,
)
from band.core.backends.observing import delivered, record_delivery, turn_context
from band.core.contracts.delivery import receipt_from_acp_chunks
from band.core.protocols import AgentToolsProtocol, EventSink
from band.integrations.acp.types import ChunkType, CollectedChunk
from band.runtime.narration import tool_call_content, tool_result_content
from band.runtime.tools import canonical_tool_name

logger = logging.getLogger(__name__)


def chunk_to_turn_event(chunk: CollectedChunk) -> TurnEvent | None:
    """Map a finalized ACP chunk onto the published ``TurnEvent`` vocabulary."""
    match chunk.chunk_type:
        case ChunkType.TEXT:
            if not chunk.content:
                return None
            return TextDeltaEvent(content=chunk.content)
        case ChunkType.THOUGHT:
            return ThoughtEvent(content=chunk.content)
        case ChunkType.TOOL_CALL:
            return ToolCallEvent(
                tool_name=canonical_tool_name(chunk.content or "unknown"),
                tool_call_id=_meta_str(chunk.metadata, "tool_call_id"),
                arguments=_tool_arguments(chunk.metadata),
                status=_tool_status(chunk.metadata.get("status")),
            )
        case ChunkType.TOOL_RESULT:
            return ToolResultEvent(
                tool_name=_result_tool_name(chunk),
                tool_call_id=_meta_str(chunk.metadata, "tool_call_id"),
                content=chunk.content,
                status=_tool_status(chunk.metadata.get("status"))
                or ToolStatus.COMPLETED,
            )
        case ChunkType.PLAN:
            return PlanEvent(content=chunk.content)
        case _:
            return None


def _result_tool_name(chunk: CollectedChunk) -> str:
    """The canonical name of the tool a result answers, or a neutral stand-in."""
    return canonical_tool_name(str(chunk.metadata.get("tool_name") or "tool"))


def _meta_str(metadata: dict[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    if value is None or value == "":
        return None
    return str(value)


def _tool_arguments(metadata: dict[str, Any]) -> dict[str, Any] | None:
    raw = metadata.get("raw_input")
    if isinstance(raw, dict):
        return dict(raw)
    return None


def _turn_sink(tools: AgentToolsProtocol) -> EventSink | None:
    """The sink of the turn these ``tools`` belong to, if it has one."""
    turn = turn_context(tools)
    return turn.events if turn is not None else None


def _tool_status(value: object) -> ToolStatus | None:
    if value is None:
        return None
    if isinstance(value, ToolStatus):
        return value
    try:
        return ToolStatus(str(value))
    except ValueError:
        return None


class RoomTurnEmitter:
    """Posts one ACP turn's output to a Band room in causal order.

    A turn's events arrive as a live stream — ``emit`` is called per finalized
    chunk — so they interleave correctly with the two things that already post
    mid-turn: a denied-permission pair (``open_permission``) and a Band messaging
    tool's own room post (a remote/injected band-mcp calling the REST API as it
    runs). Every tool call is narrated (thought, tool_call, tool_result, plan) as
    it arrives — including Band messaging tools, so a call to ``band_send_message``
    shows its real ``tool_call``/``tool_result`` straddling the message it posts,
    with no special-casing needed. The ordering is enforced upstream by
    ``ACPCollectingClient``'s per-session lock — ``emit`` is never entered
    concurrently for one session. The assistant's text reply is held until close,
    because whether to relay it depends on whether the whole turn already posted
    via a Band tool — if so the text would duplicate the reply already in the room.

    When the turn carries an ``EventSink`` (via ``AgentStream.observe`` /
    the per-turn tools proxy), each finalized chunk is also dual-written as a
    ``TurnEvent`` so ACP turns are observable end-to-end on the published stream.
    Text deltas are observed when finalized even though the room post is held
    until close.

    On a clean close the held text is relayed (unless already posted in-room), and
    the session bookkeeping ``task`` event is posted last.
    """

    def __init__(
        self,
        tools: AgentToolsProtocol,
        *,
        mentions: list[dict[str, str]],
        session_id: str,
        room_id: str,
        events: EventSink | None = None,
    ) -> None:
        self._tools = tools
        self._mentions = mentions
        self._session_id = session_id
        self._room_id = room_id
        # Resolved here, not per emit: chunks arrive on the ACP connection's
        # dispatcher task, which cannot see anything scoped to the turn's task.
        self._events = events if events is not None else _turn_sink(tools)
        self._chunks: list[CollectedChunk] = []
        self._pending_text: list[str] = []

    async def _observe(self, event: TurnEvent) -> None:
        if self._events is None:
            return
        await self._events.emit(event)

    def _note_out_of_process_delivery(self) -> None:
        """Fold a room post the turn's tools never saw into its receipt.

        An ACP tool call may run in a remote band-mcp the SDK never sees
        execute, so the session-update stream is its only trace. Detection
        matches the collected tool-call chunks by their reported title, since
        ACP has no structured tool-name field, and counts a room-posting call
        once it (or its result update) reports ``completed`` — a failed post
        must not suppress the text fallback, or the turn goes silent.
        """
        if delivered(self._tools) is None:
            record_delivery(self._tools, receipt_from_acp_chunks(self._chunks))

    async def emit(self, chunk: CollectedChunk) -> None:
        self._chunks.append(chunk)
        self._note_out_of_process_delivery()
        observed = chunk_to_turn_event(chunk)
        if observed is not None:
            await self._observe(observed)
        match chunk.chunk_type:
            case ChunkType.TEXT:
                if chunk.content:
                    self._pending_text.append(chunk.content)
            case ChunkType.THOUGHT:
                await self._tools.send_event(
                    content=chunk.content,
                    message_type="thought",
                    metadata=chunk.metadata,
                )
            case ChunkType.TOOL_CALL:
                await self._tools.send_event(
                    content=tool_call_content(
                        canonical_tool_name(chunk.content or "tool"),
                        args=_tool_arguments(chunk.metadata),
                        tool_call_id=_meta_str(chunk.metadata, "tool_call_id"),
                    ),
                    message_type=chunk.chunk_type,
                    metadata=chunk.metadata,
                )
            case ChunkType.TOOL_RESULT:
                status = _tool_status(chunk.metadata.get("status"))
                await self._tools.send_event(
                    content=tool_result_content(
                        _result_tool_name(chunk),
                        output=chunk.content,
                        tool_call_id=_meta_str(chunk.metadata, "tool_call_id"),
                        is_error=None
                        if status is None
                        else status == ToolStatus.FAILED,
                    ),
                    message_type=chunk.chunk_type,
                    metadata=chunk.metadata,
                )
            case ChunkType.PLAN:
                await self._tools.send_event(
                    content=chunk.content,
                    message_type="task",
                    metadata=chunk.metadata,
                )
            case _:
                logger.warning(
                    "Unhandled ACP chunk type %r; not posting to the room",
                    chunk.chunk_type,
                )

    async def open_permission(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        session_id: str,
        outcome: str,
    ) -> None:
        """Post a denied permission request as a ``tool_call``/``tool_result`` pair.

        Only called for a denied request: the tool never runs, so there is no
        execution frame to show it happened — this synthetic pair is the only
        record. An approved request grants silently; if the tool then executes,
        its own real ``tool_call``/``tool_result`` narrate it like any other tool.
        """
        metadata: dict[str, object] = {
            "permission_request": True,
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "acp_session_id": session_id,
            "auto_allowed": False,
        }
        await self._observe(
            ToolCallEvent(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                status=ToolStatus.IN_PROGRESS,
            )
        )
        await self._tools.send_event(
            content=tool_call_content(
                tool_name,
                args={"permission_request": True},
                tool_call_id=tool_call_id,
            ),
            message_type="tool_call",
            metadata=metadata,
        )
        await self._observe(
            ToolResultEvent(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                content=f"Permission {outcome}",
                status=ToolStatus.FAILED,
            )
        )
        await self._tools.send_event(
            content=tool_result_content(
                tool_name,
                output=f"Permission {outcome}",
                tool_call_id=tool_call_id,
                is_error=True,
            ),
            message_type="tool_result",
            metadata={**metadata, "permission_outcome": outcome},
        )

    async def observe_failure(self, error: BaseException) -> None:
        """Dual-write a turn failure onto the bound sink (room post is separate)."""
        message = f"ACP agent error: {error}"
        await self._observe(ErrorEvent(content=message))
        await self._observe(
            RunFailedEvent(
                message=message,
                retryable=False,
                error_type=type(error).__name__,
            )
        )

    async def __aenter__(self) -> RoomTurnEmitter:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        # A failed turn is handled by on_message (error event + respawn); post
        # neither the held text nor the bookkeeping event — but still dual-write
        # RunFailed onto the observation sink so AgentStream consumers see it.
        if exc_type is not None:
            if isinstance(exc, BaseException):
                await self.observe_failure(exc)
            return False
        # Tool-first delivery (matches copilot_sdk / codex): if the turn posted via
        # a Band messaging tool, relaying its plain text too would duplicate the
        # reply (and leak the agent's narration of the call). One question, even
        # though an ACP turn's Band tools may run either side of the process
        # boundary — the observer witnessed the in-process calls, and
        # ``_note_out_of_process_delivery`` gave it the rest.
        if delivered(self._tools) is None:
            for text in self._pending_text:
                await self._tools.send_message(content=text, mentions=self._mentions)
        await self._tools.send_event(
            content="ACP client session",
            message_type="task",
            metadata={
                "acp_client_session_id": self._session_id,
                "acp_client_room_id": self._room_id,
            },
        )
        return False
