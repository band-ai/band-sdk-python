"""Live, causally-ordered emission of one ACP turn's output to a Band room."""

from __future__ import annotations

import logging

from band.core.protocols import AgentToolsProtocol
from band.integrations.acp.types import ChunkType, CollectedChunk, ToolStatus
from band.runtime.tools import is_room_posting_tool

logger = logging.getLogger(__name__)


def turn_replied_in_room(chunks: list[CollectedChunk]) -> bool:
    """True when the turn posted to the room via a Band messaging tool.

    Unlike copilot_sdk / codex, which execute Band tools in-process and flip a flag
    at execution time, ACP tool calls may run out-of-process (a remote band-mcp
    server the SDK never sees execute). The ACP session-update stream is the one
    record of the turn that covers both, so detection matches the collected
    tool-call chunks by their reported title (ACP has no structured tool-name
    field). A room-posting call counts once it (or its result update) reports
    ``completed`` — a failed post must not suppress the text fallback, or the turn
    goes silent.
    """
    posting_call_ids: set[str] = set()
    for chunk in chunks:
        metadata = chunk.metadata or {}
        call_id = str(metadata.get("tool_call_id", ""))
        if chunk.chunk_type == ChunkType.TOOL_CALL and is_room_posting_tool(
            chunk.content
        ):
            if metadata.get("status") == ToolStatus.COMPLETED:
                return True
            # Correlate with a later result only by a real id. An empty id (a
            # missing tool_call_id) would match any other id-less result — e.g. a
            # non-posting tool's — and falsely suppress the text fallback,
            # silencing the turn.
            if call_id:
                posting_call_ids.add(call_id)
        elif (
            chunk.chunk_type == ChunkType.TOOL_RESULT
            and call_id in posting_call_ids
            and metadata.get("status") == ToolStatus.COMPLETED
        ):
            return True
    return False


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
    ) -> None:
        self._tools = tools
        self._mentions = mentions
        self._session_id = session_id
        self._room_id = room_id
        self._chunks: list[CollectedChunk] = []
        self._pending_text: list[str] = []

    async def emit(self, chunk: CollectedChunk) -> None:
        self._chunks.append(chunk)
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
            case ChunkType.TOOL_CALL | ChunkType.TOOL_RESULT:
                await self._tools.send_event(
                    content=chunk.content,
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
        await self._tools.send_event(
            content=f"Permission requested: {tool_name}",
            message_type="tool_call",
            metadata=metadata,
        )
        await self._tools.send_event(
            content=f"Permission {outcome}",
            message_type="tool_result",
            metadata={**metadata, "permission_outcome": outcome},
        )

    async def __aenter__(self) -> RoomTurnEmitter:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        # A failed turn is handled by on_message (error event + respawn); post
        # neither the held text nor the bookkeeping event.
        if exc_type is not None:
            return False
        # Tool-first delivery (matches copilot_sdk / codex): if the turn posted via
        # a Band messaging tool, relaying its plain text too would duplicate the
        # reply (and leak the agent's narration of the call).
        if not turn_replied_in_room(self._chunks):
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
