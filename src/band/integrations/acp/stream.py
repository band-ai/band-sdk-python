"""Coalesce ACP deltas and publish completed tool lifecycles in order."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from band.integrations.acp.results import fold_result
from band.integrations.acp.types import (
    ACPToolCall,
    ACPToolResult,
    ChunkType,
    CollectedChunk,
    ToolStatus,
)

logger = logging.getLogger(__name__)
ChunkSink = Callable[[CollectedChunk], Awaitable[None]]


class ACPChunkStream:
    """Buffer and publish output; the caller serializes operations per session."""

    _COALESCED_CHUNK_TYPES = (ChunkType.TEXT, ChunkType.THOUGHT)

    def __init__(self) -> None:
        self._session_chunks: dict[str, list[CollectedChunk]] = {}
        self._result_chunks: dict[str, dict[str, CollectedChunk]] = {}
        self._tool_calls: dict[str, dict[str, ACPToolCall]] = {}
        self._emitted_results: dict[str, set[str]] = {}
        self._open_runs: dict[str, CollectedChunk] = {}
        self._sinks: dict[str, ChunkSink] = {}

    async def ingest(self, session_id: str, chunk: CollectedChunk) -> None:
        """Coalesce text deltas and publish discrete tool lifecycle boundaries."""
        if chunk.chunk_type == ChunkType.TOOL_RESULT:
            revision, chunk.tool = chunk.tool, None
            if (
                isinstance(revision, ACPToolCall)
                and self._revise_held_call(session_id, revision)
                and _carries_no_result(chunk)
            ):
                return
        if isinstance(chunk.tool, ACPToolCall) and chunk.tool.tool_call_id:
            self._tool_calls.setdefault(session_id, {})[chunk.tool.tool_call_id] = (
                chunk.tool
            )
        if chunk.chunk_type in self._COALESCED_CHUNK_TYPES:
            open_run = self._open_runs.get(session_id)
            if open_run is not None and open_run.chunk_type == chunk.chunk_type:
                open_run.content += chunk.content  # merge the streamed delta
                return
            await self.close_open_run(session_id)
            self._open_runs[session_id] = chunk
            return
        await self.close_open_run(session_id)
        match chunk.chunk_type:
            case ChunkType.TOOL_RESULT:
                await self._ingest_tool_result(session_id, chunk)
            case ChunkType.TOOL_CALL if _awaits_input(chunk):
                # Named by a later tool_call_update (Cursor's "MCP: tool"), so it
                # is held like an open run until then.
                self._open_runs[session_id] = chunk
            case _:
                await self.finalize(session_id, chunk)

    def _revise_held_call(self, session_id: str, revision: ACPToolCall) -> bool:
        """Apply ``revision`` to the held input-less call it names, if any."""
        held = self._open_runs.get(session_id)
        if (
            held is None
            or not isinstance(held.tool, ACPToolCall)
            or held.tool.tool_call_id != revision.tool_call_id
        ):
            return False
        held.tool, held.content = revision, revision.name
        held.metadata["raw_input"] = revision.arguments
        self._tool_calls.setdefault(session_id, {})[revision.tool_call_id] = revision
        return True

    async def close_open_run(self, session_id: str) -> None:
        """Finalize the open text/thought run, if any — a boundary was reached."""
        run = self._open_runs.pop(session_id, None)
        if run is not None:
            await self.finalize(session_id, run)

    async def _ingest_tool_result(self, session_id: str, chunk: CollectedChunk) -> None:
        """Publish a tool result once, when its call first becomes terminal."""
        call_id = str(chunk.metadata.get("tool_call_id", ""))
        call = self._tool_calls.get(session_id, {}).get(call_id)
        if call is None:
            call = ACPToolCall(tool_call_id=call_id, name="unknown", arguments={})
        chunk.tool = ACPToolResult(
            call=call,
            output=chunk.content,
            status=chunk.metadata.get("status"),
        )
        if not call_id:
            await self.finalize(session_id, chunk)
            return
        results = self._result_chunks.setdefault(session_id, {})
        canonical = results.get(call_id)
        if canonical is None:
            results[call_id] = chunk
            canonical = chunk
        else:
            fold_result(canonical, chunk)
        emitted = self._emitted_results.setdefault(session_id, set())
        terminal = canonical.metadata.get("status") in (
            ToolStatus.COMPLETED,
            ToolStatus.FAILED,
        )
        # Room events are append-only; later revisions stay in the buffer.
        if terminal and call_id not in emitted:
            emitted.add(call_id)
            await self.finalize(session_id, canonical)

    async def finalize(self, session_id: str, chunk: CollectedChunk) -> None:
        """Buffer and publish a chunk; sink errors must survive ACP exception suppression."""
        self._session_chunks.setdefault(session_id, []).append(chunk)
        sink = self._sinks.get(session_id)
        if sink is None:
            return
        try:
            await sink(chunk)
        except Exception:
            logger.exception(
                "Failed to post %s chunk for ACP session %s to the room; "
                "narration for this turn may be incomplete",
                chunk.chunk_type,
                session_id,
            )

    def set_sink(self, session_id: str, sink: ChunkSink | None) -> None:
        if sink is None:
            self._sinks.pop(session_id, None)
        else:
            self._sinks[session_id] = sink

    async def flush(self, session_id: str) -> None:
        """Finalize anything still open at turn end: the coalesced run, then any
        tool result whose call never reported a terminal status."""
        await self.close_open_run(session_id)
        emitted = self._emitted_results.setdefault(session_id, set())
        for call_id, canonical in self._result_chunks.get(session_id, {}).items():
            if call_id not in emitted:
                emitted.add(call_id)
                await self.finalize(session_id, canonical)

    def get_collected_text(self, session_id: str | None = None) -> str:
        if session_id is not None:
            chunks = self._session_chunks.get(session_id, [])
        else:
            chunks = [
                chunk
                for session_chunks in self._session_chunks.values()
                for chunk in session_chunks
            ]
        return "".join(
            chunk.content for chunk in chunks if chunk.chunk_type == ChunkType.TEXT
        )

    def get_collected_chunks(
        self, session_id: str | None = None
    ) -> list[CollectedChunk]:
        if session_id is not None:
            return list(self._session_chunks.get(session_id, []))
        return [
            chunk
            for session_chunks in self._session_chunks.values()
            for chunk in session_chunks
        ]

    def reset_session(self, session_id: str) -> None:
        self._session_chunks.pop(session_id, None)
        self._result_chunks.pop(session_id, None)
        self._tool_calls.pop(session_id, None)
        self._emitted_results.pop(session_id, None)
        self._open_runs.pop(session_id, None)
        self._sinks.pop(session_id, None)


def _awaits_input(chunk: CollectedChunk) -> bool:
    """True for a pending tool_call reported before its input."""
    return chunk.metadata.get(
        "status"
    ) == ToolStatus.PENDING and not chunk.metadata.get("raw_input")


def _carries_no_result(chunk: CollectedChunk) -> bool:
    """True for a tool_call_update frame that only revised its call's identity."""
    return chunk.metadata.get("status") is None and not chunk.content
