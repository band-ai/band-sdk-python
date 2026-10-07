"""Live, causally-ordered emission of one ACP turn's output to a Band room."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Self

from band.core.delivery import handle_leftover_text
from band.core.protocols import AgentToolsProtocol, send_event_safe
from band.core.types import Emit, LeftoverText
from band.integrations.acp.types import (
    ACPToolCall,
    ACPToolResult,
    ChunkType,
    CollectedChunk,
    ToolStatus,
)
from band.runtime.tools import TurnEffect, turn_effect

logger = logging.getLogger(__name__)

ACP_SESSION_CLOSED_EVENT = "ACP client session"


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
    because whether to relay it depends on whether the whole turn already replied
    or declined via a Band tool — if so the text would duplicate the reply already
    in the room.

    On a clean close the held text is handled as ``leftover_text`` says (unless
    the turn already replied), and the session bookkeeping ``task`` event is
    posted last.

    Which narration kinds reach the room is controlled by the emit set passed
    at construction (``None``: all kinds — the historical default). The closing
    bookkeeping ``task`` event is state, not narration, and is posted regardless
    of the emit set: ``ACPClientHistoryConverter`` reads its metadata to rebuild
    the room→session map, so gating it would silently disable ``session/load``
    resume after a restart.
    """

    def __init__(
        self,
        tools: AgentToolsProtocol,
        *,
        mentions: list[dict[str, str]],
        session_id: str,
        room_id: str,
        emit: frozenset[Emit] | None = None,
        records_tool_effects: bool = False,
        leftover_text: LeftoverText = LeftoverText.REPLY,
        custom_effects: Mapping[str, TurnEffect] | None = None,
    ) -> None:
        """``records_tool_effects``: the turn's tools run out of process, so
        each completed call's effect is recorded on the turn from the stream.

        Those effects are staged and reach the turn only when the prompt closes
        successfully: a rejected, timed-out or cancelled prompt owns none of
        the work streamed during it, which may belong to another turn.

        ``custom_effects``: the effects the adapter's custom tools declared,
        so a refused custom reply tool is recognised as a failed reply.
        """
        self._tools = tools
        self._mentions = mentions
        self._session_id = session_id
        self._room_id = room_id
        self._records_tool_effects = records_tool_effects
        self._leftover_text = leftover_text
        self._custom_effects = custom_effects
        # ``None``: post every kind (the historical behavior). Adapters pass
        # their resolved ``features.emit`` so a caller's ``emit=`` narrowing
        # reaches the room sink.
        self._emit = frozenset(emit) if emit is not None else frozenset(Emit)
        self._pending_text: list[str] = []
        self._staged_effects: list[TurnEffect] = []
        self._reply_attempted = False

    async def emit(self, chunk: CollectedChunk) -> None:
        self._stage_tool_outcome(chunk)
        match chunk.chunk_type:
            case ChunkType.TEXT:
                if chunk.content:
                    self._pending_text.append(chunk.content)
            case ChunkType.THOUGHT:
                if Emit.THOUGHTS not in self._emit:
                    return
                await self._tools.send_event(
                    content=chunk.content,
                    message_type="thought",
                    metadata=chunk.metadata,
                )
            case ChunkType.TOOL_CALL | ChunkType.TOOL_RESULT:
                if Emit.TOOL_CALLS not in self._emit:
                    return
                await self._tools.send_event(
                    content=self._tool_event_content(chunk),
                    message_type=chunk.chunk_type,
                    metadata=chunk.metadata,
                )
            case ChunkType.PLAN:
                if Emit.TASK_EVENTS not in self._emit:
                    return
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

    def _stage_tool_outcome(self, chunk: CollectedChunk) -> None:
        """Stage what a tool call means for the turn.

        ACP has no structured tool-name field, so the canonicalized title names
        the tool; a non-Band tool resolves to ``observe``. A completed call's
        effect is staged only for an external band-mcp, which runs where the
        SDK never sees it; injected tools record their own. A failed call
        records no effect, so a failed post never suppresses the text relay.
        Any call to a reply tool, in either mode and whatever its status, is a
        reply attempt: an injected tool can be refused (its arguments failed
        validation, its permission was denied) before it runs, so the stream
        is the only record that the model tried to answer.
        """
        match chunk.tool:
            case ACPToolCall(name=name) | ACPToolResult(call=ACPToolCall(name=name)):
                self._note_reply_attempt(name)
            case _:
                return
        if (
            self._records_tool_effects
            and chunk.metadata.get("status") == ToolStatus.COMPLETED
        ):
            self._staged_effects.append(turn_effect(name))

    def _note_reply_attempt(self, tool_name: str) -> None:
        effect = turn_effect(tool_name, custom_effects=self._custom_effects)
        if effect is TurnEffect.REPLY:
            self._reply_attempted = True

    def _tool_event_content(self, chunk: CollectedChunk) -> str:
        """Serialize normalized tool activity for room persistence."""
        if isinstance(chunk.tool, (ACPToolCall, ACPToolResult)):
            return chunk.tool.room_event().model_dump_json()
        raise RuntimeError("ACP tool chunk is missing normalized tool activity")

    async def open_permission(
        self,
        *,
        call: ACPToolCall,
        session_id: str,
        outcome: str,
    ) -> None:
        """Post a denied permission request as a ``tool_call``/``tool_result`` pair.

        Only called for a denied request: the tool never runs, so there is no
        execution frame to show it happened — this synthetic pair is the only
        record. An approved request grants silently; if the tool then executes,
        its own real ``tool_call``/``tool_result`` narrate it like any other tool.
        The pair is part of tool-call narration, so it is suppressed when
        ``Emit.TOOL_CALLS`` is not in the emitter's emit set.
        """
        self._note_reply_attempt(call.name)
        if Emit.TOOL_CALLS not in self._emit:
            return
        metadata: dict[str, object] = {
            "permission_request": True,
            "tool_name": call.name,
            "tool_call_id": call.tool_call_id,
            "acp_session_id": session_id,
            "auto_allowed": False,
        }
        await self._tools.send_event(
            content=call.room_event().model_dump_json(),
            message_type="tool_call",
            metadata=metadata,
        )
        result = ACPToolResult(
            call=call,
            output=f"Permission {outcome}",
            status=ToolStatus.FAILED,
        )
        await self._tools.send_event(
            content=result.room_event().model_dump_json(),
            message_type="tool_result",
            metadata={**metadata, "permission_outcome": outcome},
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        # A failed turn is handled by on_message (error event + respawn); post
        # neither the held text nor the bookkeeping event.
        if exc_type is not None:
            return False
        for effect in self._staged_effects:
            self._tools.turn.record(effect)
        if self._reply_attempted:
            self._tools.turn.note_reply_attempt()
        # The held runs only ever post together at close, so they are handled
        # as the turn's one closing text.
        await handle_leftover_text(
            self._tools,
            "\n\n".join(self._pending_text),
            self._mentions,
            mode=self._leftover_text,
            emit=self._emit,
        )
        # Posted regardless of the emit set: this is resume state read back by
        # ACPClientHistoryConverter, not narration (only PLAN chunks follow
        # Emit.TASK_EVENTS).
        await send_event_safe(
            self._tools,
            content=ACP_SESSION_CLOSED_EVENT,
            message_type="task",
            metadata={
                "acp_client_session_id": self._session_id,
                "acp_client_room_id": self._room_id,
            },
            log_label=ACP_SESSION_CLOSED_EVENT,
        )
        return False
