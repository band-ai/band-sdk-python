"""Generic ACP subprocess runtime for outbound ACP bridges."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Literal, Protocol, cast

from acp import connect_to_agent, spawn_agent_process, text_block
from acp.exceptions import RequestError
from acp.interfaces import Client

from band.integrations.acp.client_profiles import (
    ACPClientProfile,
    NoopACPClientProfile,
)
from band.integrations.acp.types import ChunkType, CollectedChunk, ToolStatus

logger = logging.getLogger(__name__)

ACP_STDIO_LIMIT_BYTES = 16 * 1024 * 1024
ACP_SESSION_LOAD_TIMEOUT_SECONDS = 5.0
PermissionHandler = Callable[..., Awaitable[dict[str, object]]]
ChunkSink = Callable[[CollectedChunk], Awaitable[None]]
MCPTransportKind = Literal["http", "sse"]

# ACP grants a tool-call permission by *selecting one of the options the agent
# offered* (each carries an ``optionId`` and a ``kind``); the on-wire response is
# ``{"outcome": {"outcome": "selected", "optionId": ...}}`` or
# ``{"outcome": {"outcome": "cancelled"}}`` (see ``acp.schema`` AllowedOutcome /
# DeniedOutcome). There is no ``"allowed"`` literal — emitting one makes a
# spec-strict agent (e.g. codex-acp) fail to parse the response and abort the turn.
_ALLOW_OPTION_KINDS = ("allow_once", "allow_always")


def select_allow_option_id(options: object) -> str | None:
    """The ``optionId`` of an allow option offered in a permission request, else None.

    Prefers the least-privilege ``allow_once`` over ``allow_always``. Returns None
    when the agent offered no allow option, so the caller cancels rather than
    guessing (selecting a reject option would silently deny). Accepts the ACP
    ``PermissionOption`` objects or plain dicts.
    """
    if not isinstance(options, (list, tuple)):
        return None
    candidates: list[tuple[object, str]] = []
    for option in options:
        if isinstance(option, dict):
            kind = option.get("kind")
            # Coalesce the camelCase (wire/JSON) and snake_case spellings on
            # *absence*, not falsiness — an explicit (if empty) id must not fall
            # through to the alias and get dropped.
            option_id = option.get("optionId")
            if option_id is None:
                option_id = option.get("option_id")
        else:
            kind = getattr(option, "kind", None)
            option_id = getattr(option, "option_id", None)
            if option_id is None:
                option_id = getattr(option, "optionId", None)
        if option_id is not None:
            candidates.append((kind, str(option_id)))
    for preferred in _ALLOW_OPTION_KINDS:
        for kind, option_id in candidates:
            if kind == preferred:
                return option_id
    return None


def allow_permission(option_id: str) -> dict[str, object]:
    """An ACP ``RequestPermissionResponse`` selecting (granting) ``option_id``."""
    return {"outcome": {"outcome": "selected", "optionId": option_id}}


def cancel_permission() -> dict[str, object]:
    """An ACP ``RequestPermissionResponse`` cancelling the request."""
    return {"outcome": {"outcome": "cancelled"}}


def _strict_json_equal(a: object, b: object) -> bool:
    """JSON equality without Python's cross-type coercions.

    ``==`` treats ``True == 1`` and ``1 == 1.0`` as equal, which would let two
    genuinely different JSON payloads pass a duplicate-echo proof. Two values
    are equal here only if their JSON types match too, recursively.
    """
    if type(a) is not type(b):
        return False
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(
            _strict_json_equal(value, b[key]) for key, value in a.items()
        )
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_strict_json_equal(x, y) for x, y in zip(a, b))
    return a == b


def _is_echo_of(content: str, readable: str, echo: dict[str, object]) -> bool:
    """True when ``content`` is ``readable`` followed by one JSON re-encoding of
    exactly ``echo`` -- the one proven duplicated-echo shape, never a guessed
    one. The trailing segment is parsed, not string-compared, so the
    re-encoding's separators/escaping don't matter.
    """
    if not content.startswith(readable):
        return False
    trailing = content[len(readable) :].strip()
    try:
        return _strict_json_equal(json.loads(trailing), echo)
    except json.JSONDecodeError:
        return False


def _readable_rendering(content: str, structured: dict[str, object]) -> str | None:
    """The prefix of ``content`` that renders ``structured``, else ``None``.

    A FastMCP primitive wrap (``{"result": <string>}``) renders as the wrapped
    string verbatim -- required non-empty, or the "rendering" is vacuous and
    proves nothing; any other object renders as a leading JSON document that
    parses equal to ``structured``.
    """
    result = structured.get("result")
    if set(structured) == {"result"} and isinstance(result, str):
        return result if result and content.startswith(result) else None
    try:
        leading_value, end = json.JSONDecoder().raw_decode(content)
    except json.JSONDecodeError:
        return None
    return content[:end] if _strict_json_equal(leading_value, structured) else None


def _unwrap_structured_result(
    content: str, raw_output: object
) -> tuple[str, dict[str, object]] | None:
    """Recover a tool result's readable value from a duplicated structured echo.

    An MCP bridge (observed: Copilot) can forward both a tool result's readable
    text and its ``structuredContent`` companion into one text block. The echo
    invariant, stated once: ``content`` is a readable rendering of
    ``structuredContent`` (see ``_readable_rendering``) followed by exactly one
    JSON re-encoding of ``structuredContent`` (see ``_is_echo_of``). On that
    full proof, returns ``(readable, echo)`` -- the cleaned value plus the
    payload proven appended, which the chunk records so a later frame
    re-reporting the same duplicate is recognized by exactly that shape.

    Returns ``None`` (leave ``content`` untouched) for anything less: a bridge
    may synthesize a distinct human-facing ``content`` -- a summary, or prose
    that merely quotes the structured value -- alongside a tool's real
    structured result, and that legitimate text must never be clobbered just
    because a shape matches or the value appears somewhere within it.
    """
    if not isinstance(raw_output, dict):
        return None
    structured = raw_output.get("structuredContent")
    if not isinstance(structured, dict):
        return None
    readable = _readable_rendering(content, structured)
    if readable is not None and _is_echo_of(content, readable, structured):
        return readable, structured
    return None


def tcp_spawn_process(
    host: str,
    port: int,
    *,
    limit: int = ACP_STDIO_LIMIT_BYTES,
) -> Callable[..., AbstractAsyncContextManager[tuple[object, object]]]:
    """Build a ``spawn_process`` callable that connects to an ACP server over TCP.

    Drop-in for the stdio ``spawn_agent_process`` seam in :class:`ACPRuntime`: the
    runtime dials *into* an already-running ACP server (e.g. ``copilot --acp --port
    N`` in a container) instead of spawning a subprocess. The returned callable
    accepts and ignores the subprocess-shaped args the runtime forwards (the
    command executable/args and ``transport_kwargs``) — host/port are captured
    here — so no core change to ``ACPRuntime.start`` is needed.
    """

    @asynccontextmanager
    async def _connect(
        client: Client,
        *_command: object,
        env: dict[str, str] | None = None,
        transport_kwargs: dict[str, object] | None = None,
    ) -> AsyncIterator[tuple[object, object]]:
        del _command, env, transport_kwargs  # subprocess-only; unused for TCP
        reader, writer = await asyncio.open_connection(host, port, limit=limit)
        # connect_to_agent argument order is (client, input_stream=writer,
        # output_stream=reader) and it type-guards writer: StreamWriter /
        # reader: StreamReader. Unlike spawn_agent_process it does no cleanup,
        # so we close the connection and transport ourselves.
        conn = connect_to_agent(client, writer, reader)
        try:
            yield conn, writer
        finally:
            try:
                await conn.close()
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    logger.debug("Error awaiting TCP writer close", exc_info=True)

    return _connect


class ACPConnectionProtocol(Protocol):
    """Protocol for the ACP agent connection returned by spawn_agent_process."""

    async def initialize(self, *, protocol_version: int) -> object: ...

    async def authenticate(self, *, method_id: str) -> object: ...

    async def new_session(self, *, cwd: str, mcp_servers: list[object]) -> object: ...

    async def load_session(
        self,
        *,
        cwd: str,
        session_id: str,
        mcp_servers: list[object],
    ) -> object: ...

    async def prompt(self, *, session_id: str, prompt: list[object]) -> object: ...


class ACPSpawnContextProtocol(Protocol):
    """Protocol for the spawn_agent_process async context manager."""

    async def __aenter__(self) -> tuple[ACPConnectionProtocol, object]: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> object: ...


class ACPNewSessionProtocol(Protocol):
    """Protocol for ACP session creation responses."""

    session_id: str


class ACPCollectingClient(Client):  # type: ignore[misc]  # ACP Client has optional methods treated as abstract by pyrefly
    """Generic ACP client that buffers session updates by session_id.

    The ``acp`` transport runs each incoming notification as its own task, so
    consecutive ``session_update``s execute concurrently. A per-session lock
    serializes the ingest→sink path and the permission handler (which posts to
    the same room mid-turn): lock waiters wake FIFO and the tasks start in
    wire-arrival order, so room posts keep the stream's causal order.
    """

    def __init__(self, profile: ACPClientProfile | None = None) -> None:
        self._profile = profile or NoopACPClientProfile()
        self._session_chunks: dict[str, list[CollectedChunk]] = {}
        self._permission_handlers: dict[str, PermissionHandler] = {}
        # Per session, the canonical tool_result chunk for each tool_call_id, so a
        # call's stream of tool_call_updates folds into one result, finalized once
        # when the call reaches a terminal status (see _ingest_tool_result). Reset
        # per turn in reset_session.
        self._result_chunks: dict[str, dict[str, CollectedChunk]] = {}
        self._emitted_results: dict[str, set[str]] = {}
        # An open text/thought run being coalesced until the next boundary, and the
        # per-session live sink that finalized chunks are posted to, in order.
        self._open_runs: dict[str, CollectedChunk] = {}
        self._sinks: dict[str, ChunkSink] = {}
        # Never popped in reset_session: replacing a lock a straggler task still
        # holds would let two tasks into the session's critical section.
        self._session_locks: dict[str, asyncio.Lock] = {}

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        return self._session_locks.setdefault(session_id, asyncio.Lock())

    async def session_update(
        self, session_id: str, update: object, **kwargs: object
    ) -> None:
        del kwargs
        chunk = self._chunk_from_update(update)
        if chunk is not None:
            async with self._session_lock(session_id):
                await self._ingest(session_id, chunk)

    def _chunk_from_update(self, update: object) -> CollectedChunk | None:
        """Parse one ACP session update without mutating the chunk buffer."""
        match getattr(update, "session_update", None):
            case "agent_message_chunk":
                return self._text_chunk(update, ChunkType.TEXT)
            case "agent_thought_chunk":
                return self._text_chunk(update, ChunkType.THOUGHT)
            case "tool_call":
                return self._tool_call_chunk(update)
            case "tool_call_update":
                return self._tool_result_chunk(update)
            case "plan":
                entries = getattr(update, "entries", [])
                plan_text = "\n".join(
                    getattr(entry, "content", str(entry)) for entry in entries
                )
                return CollectedChunk(chunk_type=ChunkType.PLAN, content=plan_text)
            case _:
                text = self._extract_text_from_content(update)
                return (
                    CollectedChunk(chunk_type=ChunkType.TEXT, content=text)
                    if text
                    else None
                )

    def _text_chunk(self, update: object, chunk_type: str) -> CollectedChunk:
        return CollectedChunk(
            chunk_type=chunk_type,
            content=self._extract_text_from_content(update),
        )

    def _tool_call_chunk(self, update: object) -> CollectedChunk:
        tool_call_id = getattr(update, "tool_call_id", "")
        title = getattr(update, "title", "")
        metadata = {
            "tool_call_id": tool_call_id,
            "raw_input": getattr(update, "raw_input", None),
            "status": getattr(update, "status", ToolStatus.IN_PROGRESS),
        }
        return CollectedChunk(
            chunk_type=ChunkType.TOOL_CALL,
            content=title,
            metadata=metadata,
        )

    def _tool_result_chunk(self, update: object) -> CollectedChunk:
        tool_call_id = getattr(update, "tool_call_id", "")
        status = getattr(update, "status", ToolStatus.COMPLETED)
        metadata = {
            "tool_call_id": tool_call_id,
            "status": status,
        }
        # Prefer the human-readable content blocks over ``rawOutput``. An agent's
        # terminal update often carries the structured result object in
        # ``rawOutput`` (e.g. Copilot's ``{'content': ..., 'contents': [...]}``),
        # which stringifies into an unreadable dict; the content blocks hold the
        # same output as plain text. Fall back to ``rawOutput`` only when there are
        # no content blocks, so an agent that reports output *only* via
        # ``rawOutput`` still surfaces it (a blank result keeps the placeholder
        # guard). ``from_raw`` records the fallback so _merge_tool_result never
        # overwrites clean text with a later raw-only frame.
        content = self._extract_text_from_tool_content(getattr(update, "content", None))
        from_raw = not content
        echo: dict[str, object] | None = None
        if from_raw:
            raw_output = getattr(update, "raw_output", "")
            content = str(raw_output) if raw_output else ""
        else:
            # ``rawOutput`` may still carry the least-processed copy of the same
            # value under MCP's own ``structuredContent`` field (see
            # _unwrap_structured_result); prefer it over the content blocks when
            # recognizable, since a bridge that forwards both can duplicate a
            # JSON-serialized string across them.
            unwrapped = _unwrap_structured_result(
                content, getattr(update, "raw_output", None)
            )
            if unwrapped is not None:
                content, echo = unwrapped
        return CollectedChunk(
            chunk_type=ChunkType.TOOL_RESULT,
            content=content,
            metadata=metadata,
            from_raw=from_raw,
            echo=echo,
        )

    # Chunk kinds that arrive as a stream of deltas for one logical message, so a
    # run of them is coalesced into a single chunk (agents emit one delta per token
    # or phrase). tool_call/tool_result/plan are discrete and never merged.
    _COALESCED_CHUNK_TYPES = (ChunkType.TEXT, ChunkType.THOUGHT)

    async def _ingest(self, session_id: str, chunk: CollectedChunk) -> None:
        """Route one parsed chunk through coalescing/collapse to the live sink.

        Text/thought deltas coalesce into an open run, finalized at the next
        boundary (a different chunk type, or turn end). tool_call and plan are
        discrete and finalize at once. A tool_result folds its call's frames and
        finalizes when the call reaches a terminal status. Finalizing a chunk both
        buffers it (for get_collected_chunks) and posts it to the sink, in order.
        """
        if chunk.chunk_type in self._COALESCED_CHUNK_TYPES:
            open_run = self._open_runs.get(session_id)
            if open_run is not None and open_run.chunk_type == chunk.chunk_type:
                open_run.content += chunk.content  # merge the streamed delta
                return
            await self._close_open_run(session_id)
            self._open_runs[session_id] = chunk
            return
        await self._close_open_run(session_id)
        if chunk.chunk_type == ChunkType.TOOL_RESULT:
            await self._ingest_tool_result(session_id, chunk)
        else:
            await self._finalize(session_id, chunk)

    async def _close_open_run(self, session_id: str) -> None:
        """Finalize the open text/thought run, if any — a boundary was reached."""
        run = self._open_runs.pop(session_id, None)
        if run is not None:
            await self._finalize(session_id, run)

    async def _ingest_tool_result(self, session_id: str, chunk: CollectedChunk) -> None:
        """Fold a tool_result frame into its call and finalize once, at terminal.

        A call reports its result over several frames sharing a ``tool_call_id``
        (partial content blocks, then a terminal frame often carrying only the
        structured ``rawOutput``). They fold into one canonical result, finalized
        the first time the call reports a terminal status. A frame with no id can't
        be correlated, so it stands alone.
        """
        call_id = str(chunk.metadata.get("tool_call_id", ""))
        if not call_id:
            await self._finalize(session_id, chunk)
            return
        results = self._result_chunks.setdefault(session_id, {})
        canonical = results.get(call_id)
        if canonical is None:
            results[call_id] = chunk
            canonical = chunk
        else:
            self._fold_result(canonical, chunk)
        emitted = self._emitted_results.setdefault(session_id, set())
        terminal = canonical.metadata.get("status") in (
            ToolStatus.COMPLETED,
            ToolStatus.FAILED,
        )
        # Finalize exactly once, at the first terminal frame — the earliest point a
        # live, causally-ordered post is correct (waiting for the true last frame
        # would defer every tool_result to turn-end, out of order). A later frame
        # still folds into ``canonical`` (so get_collected_chunks reflects it), but
        # the room event was already posted: the events API is append-only, so we
        # can neither edit it nor re-post without duplicating the narration. A
        # post-terminal content revision therefore stays in the buffer only — an
        # accepted trade-off of live emission, not a bug to "fix" by re-emitting.
        if terminal and call_id not in emitted:
            emitted.add(call_id)
            await self._finalize(session_id, canonical)

    def _fold_result(self, canonical: CollectedChunk, chunk: CollectedChunk) -> None:
        """Fold a later frame into a call's canonical result.

        The last *reported* status wins — ACP status is optional, so a frame that
        omits it (status is None) must not regress a recorded "completed". A frame
        that merely re-reports a cleaned canonical's proven duplicate — its
        content is the cleaned value plus a re-encoding of exactly the recorded
        ``CollectedChunk.echo`` payload — carries no new information and must not
        regress the cleaned value; anything else, including a genuinely new
        readable result, still replaces it. Otherwise ACP ``content`` replaces the
        preceding content collection, so the latest readable frame wins even when
        it is shorter (e.g. a long-running command's streamed "still running..."
        progress text superseded by a short "OK"). A raw-only or empty frame,
        which carries no such replacement semantics, falls back to ranking by
        completeness (see _result_key).
        """
        incoming_status = chunk.metadata.get("status")
        if incoming_status is not None:
            canonical.metadata["status"] = incoming_status
        if canonical.echo is not None and _is_echo_of(
            chunk.content, canonical.content, canonical.echo
        ):
            best = canonical
        elif chunk.content and not chunk.from_raw:
            best = chunk
        elif canonical.content and not canonical.from_raw:
            best = canonical
        else:
            best = max(canonical, chunk, key=self._result_key)
        canonical.content, canonical.from_raw, canonical.echo = (
            best.content,
            best.from_raw,
            best.echo,
        )

    async def _finalize(self, session_id: str, chunk: CollectedChunk) -> None:
        """Buffer a finalized chunk and post it to the session's live sink, if any.

        A sink failure is logged, not raised: the ``acp`` transport suppresses
        notification-handler exceptions without a trace, so raising would lose
        the failure. Narration is best-effort — the turn's reply still posts
        (and fails loudly) from ``on_message``'s own task.
        """
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
        async with self._session_lock(session_id):
            await self._close_open_run(session_id)
            emitted = self._emitted_results.setdefault(session_id, set())
            for call_id, canonical in self._result_chunks.get(session_id, {}).items():
                if call_id not in emitted:
                    emitted.add(call_id)
                    await self._finalize(session_id, canonical)

    @staticmethod
    def _result_key(chunk: CollectedChunk) -> tuple[bool, bool, int]:
        """Rank the raw-only/empty frames ``_fold_result`` falls back to (neither
        side is a readable-content replacement, so completeness decides): non-empty
        beats empty, then the longer (more complete) frame.
        """
        has_text = bool(chunk.content)
        return has_text, has_text and not chunk.from_raw, len(chunk.content)

    async def request_permission(  # type: ignore[override]  # ACP Client uses specific types; we widen to object
        self,
        options: object,
        session_id: str,
        tool_call: object,
        **kwargs: object,
    ) -> dict[str, object]:
        handler = self._permission_handlers.get(session_id)
        if handler:
            # A denied request posts a tool_call/tool_result pair; the lock
            # keeps the pair atomic between narration posts.
            async with self._session_lock(session_id):
                return await handler(
                    options=options,
                    session_id=session_id,
                    tool_call=tool_call,
                    **kwargs,
                )

        logger.debug("Auto-cancelling permission request for session %s", session_id)
        return cancel_permission()

    def set_permission_handler(
        self,
        session_id: str,
        handler: PermissionHandler | None,
    ) -> None:
        if handler is None:
            self._permission_handlers.pop(session_id, None)
        else:
            self._permission_handlers[session_id] = handler

    def reset_session(self, session_id: str) -> None:
        self._session_chunks.pop(session_id, None)
        self._permission_handlers.pop(session_id, None)
        self._result_chunks.pop(session_id, None)
        self._emitted_results.pop(session_id, None)
        self._open_runs.pop(session_id, None)
        self._sinks.pop(session_id, None)

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

    async def ext_method(
        self,
        method: str,
        params: dict[str, object],
    ) -> dict[str, object]:
        return await self._profile.ext_method(method, params)

    async def ext_notification(self, method: str, params: dict[str, object]) -> None:
        session_id = str(params.get("sessionId") or params.get("session_id") or "")
        if not session_id:
            return

        chunks = await self._profile.ext_notification(method, params)
        if chunks:
            async with self._session_lock(session_id):
                await self._close_open_run(session_id)
                for chunk in chunks:
                    await self._finalize(session_id, chunk)

    @staticmethod
    def _block_text(block: object) -> str:
        """The ``text`` field of a single ACP content block, else ``""``.

        Accepts either the parsed pydantic model (``.text``) or a raw dict.
        """
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        return str(text) if text else ""

    @staticmethod
    def _extract_text_from_content(update: object) -> str:
        return ACPCollectingClient._block_text(getattr(update, "content", None))

    @staticmethod
    def _extract_text_from_tool_content(content: object) -> str:
        """Join the inline text blocks of a ``tool_call_update``'s ``content`` list.

        Unlike the single-block ``content`` on message/thought updates, a
        ``ToolCallUpdate.content`` is a tagged-union list (``ContentToolCallContent``
        | ``FileEditToolCallContent`` | ``TerminalToolCallContent``, discriminated
        by ``type``); only ``"content"`` entries wrap a text block, so entries of
        another ``type`` (file-edit diffs, terminal references) are skipped by
        their explicit tag rather than by happening to lack a ``.content`` field.
        """
        if not isinstance(content, list):
            return ""
        texts = [
            ACPCollectingClient._block_text(getattr(item, "content", None))
            for item in content
            if getattr(item, "type", None) == "content"
        ]
        return "\n".join(text for text in texts if text)


class ACPRuntime:
    """Generic ACP subprocess runtime shared by outbound ACP bridges."""

    def __init__(
        self,
        *,
        command: list[str],
        env: dict[str, str] | None = None,
        auth_method: str | None = None,
        client_factory: Callable[[], ACPCollectingClient] | None = None,
        spawn_process: Callable[..., object] | None = None,
    ) -> None:
        self._command = list(command)
        self._env = env
        self._auth_method = auth_method
        self._client_factory = client_factory or ACPCollectingClient
        self._spawn_process = spawn_process or spawn_agent_process

        self._conn: ACPConnectionProtocol | None = None
        self._client: ACPCollectingClient | None = None
        self._ctx: (
            AbstractAsyncContextManager[tuple[ACPConnectionProtocol, object]] | None
        ) = None
        self._stop_lock = asyncio.Lock()
        self._agent_mcp_transport: MCPTransportKind = "http"
        self._agent_supports_session_load = False

    async def start(self, *, respawn: bool = False) -> None:
        """Spawn or respawn the ACP agent subprocess."""
        logger.info(
            "%s ACP agent subprocess",
            "Respawning" if respawn else "Spawning",
        )

        self._client = self._client_factory()  # type: ignore[abstract]  # ACP client protocol defines optional hooks as abstract
        ctx = cast(
            AbstractAsyncContextManager[tuple[ACPConnectionProtocol, object]],
            self._spawn_process(
                self._client,
                # Splat the whole command: stdio forwards executable + args, while
                # a TCP transport passes an empty command (host/port live in the
                # injected spawn_process closure) and receives no positional args.
                *self._command,
                env=self._env,
                transport_kwargs={"limit": ACP_STDIO_LIMIT_BYTES},
            ),
        )
        self._ctx = ctx
        try:
            self._conn, _ = await ctx.__aenter__()
            init_response = await self._conn.initialize(protocol_version=1)
            self._agent_mcp_transport = self._select_mcp_transport(init_response)
            self._agent_supports_session_load = self._select_session_load(init_response)
            if self._auth_method:
                await self._conn.authenticate(method_id=self._auth_method)
                logger.info("Authenticated with method: %s", self._auth_method)
        except (asyncio.CancelledError, KeyboardInterrupt):
            await self._cleanup_failed_start(ctx, "init cancel")
            raise
        except Exception:
            await self._cleanup_failed_start(ctx, "init failure")
            raise
        # A connect-only transport (e.g. TCP) carries no command; describe it
        # rather than logging a blank suffix.
        logger.info(
            "Connected to ACP agent: %s",
            " ".join(self._command) or "<injected transport>",
        )

    async def ensure_connection(self, *, can_respawn: bool) -> ACPConnectionProtocol:
        async with self._stop_lock:
            if self._conn is None:
                if self._ctx is None and can_respawn:
                    await self.start(respawn=True)
                else:
                    raise RuntimeError(
                        "ACP client not initialized. Call on_started first."
                    )

            conn = self._conn

        if conn is None:
            raise RuntimeError("ACP client connection dropped before prompt")
        return conn

    async def create_session(self, *, cwd: str, mcp_servers: list[object]) -> str:
        conn = await self.ensure_connection(can_respawn=False)
        session = cast(
            ACPNewSessionProtocol,
            await conn.new_session(cwd=cwd, mcp_servers=mcp_servers),
        )
        return session.session_id

    async def load_session(
        self,
        *,
        cwd: str,
        session_id: str,
        mcp_servers: list[object],
    ) -> bool:
        """Load a persisted ACP session when the connected agent supports it.

        ACP session IDs are meaningful only to the agent process that owns them.
        A successful ``session/load`` is therefore the boundary where a persisted ID
        becomes usable on this connection. An unsupported, unavailable, or slow load
        returns ``False`` so callers can create a fresh session without blocking a turn.
        """
        if not self._agent_supports_session_load:
            return False

        conn = await self.ensure_connection(can_respawn=False)
        try:
            response = await asyncio.wait_for(
                conn.load_session(
                    cwd=cwd,
                    session_id=session_id,
                    mcp_servers=mcp_servers,
                ),
                timeout=ACP_SESSION_LOAD_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning(
                "ACP session %s did not load within %s seconds",
                session_id,
                ACP_SESSION_LOAD_TIMEOUT_SECONDS,
            )
            return False
        except RequestError as error:
            # Any load failure is equally recoverable: the caller falls back to
            # a fresh session (with history replay) rather than letting a remote
            # protocol error kill the bootstrap turn.
            if self._is_missing_session_error(error):
                logger.info("ACP session %s is no longer available", session_id)
            else:
                logger.warning(
                    "ACP session/load for %s failed (%s); using a new session",
                    session_id,
                    error,
                )
            return False
        return response is not None

    async def prompt(
        self,
        *,
        session_id: str,
        prompt_text: str,
        on_chunk: ChunkSink | None = None,
    ) -> list[CollectedChunk]:
        conn = await self.ensure_connection(can_respawn=False)
        if on_chunk is not None and self._client is not None:
            self._client.set_sink(session_id, on_chunk)
        try:
            await conn.prompt(session_id=session_id, prompt=[text_block(prompt_text)])
            if self._client is not None:
                await self._client.flush(session_id)
        finally:
            if self._client is not None:
                self._client.set_sink(session_id, None)
        return self.get_collected_chunks(session_id)

    def reset_session(self, session_id: str) -> None:
        if self._client is not None:
            self._client.reset_session(session_id)

    def set_permission_handler(
        self,
        session_id: str,
        handler: PermissionHandler | None,
    ) -> None:
        if self._client is not None:
            self._client.set_permission_handler(session_id, handler)

    def get_collected_chunks(self, session_id: str) -> list[CollectedChunk]:
        if self._client is None:
            return []
        return self._client.get_collected_chunks(session_id)

    async def stop(self) -> None:
        ctx: AbstractAsyncContextManager[tuple[ACPConnectionProtocol, object]] | None
        async with self._stop_lock:
            ctx = self._ctx
            self._ctx = None
            self._conn = None
            self._client = None
            self._agent_supports_session_load = False
        if ctx is None:
            return
        try:
            await ctx.__aexit__(None, None, None)
        except Exception:
            logger.exception("Error during ACP runtime shutdown")

    async def _cleanup_failed_start(
        self,
        ctx: AbstractAsyncContextManager[tuple[ACPConnectionProtocol, object]],
        reason: str,
    ) -> None:
        try:
            await ctx.__aexit__(None, None, None)
        except Exception:
            logger.exception("Error cleaning up ACP subprocess after %s", reason)
        self._ctx = None
        self._conn = None
        self._agent_supports_session_load = False

    @staticmethod
    def _select_mcp_transport(init_response: object) -> MCPTransportKind:
        capabilities = getattr(init_response, "agent_capabilities", None)
        mcp_capabilities = getattr(capabilities, "mcp_capabilities", None)

        if getattr(mcp_capabilities, "http", False):
            return "http"
        if getattr(mcp_capabilities, "sse", False):
            return "sse"

        return "http"

    @staticmethod
    def _select_session_load(init_response: object) -> bool:
        capabilities = getattr(init_response, "agent_capabilities", None)
        return getattr(capabilities, "load_session", False) is True

    @staticmethod
    def _is_missing_session_error(error: RequestError) -> bool:
        """Whether an ACP ``session/load`` failure means the session is absent."""
        return error.code == -32002 or (
            "session" in str(error).lower() and "not found" in str(error).lower()
        )
