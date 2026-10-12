"""ACP client callbacks with serialized session output and decisions."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence

from acp.interfaces import Client
from acp.schema import ConfigOptionUpdate, DeclineElicitationResponse

from band.integrations.acp.client_profiles import ACPClientProfile, NoopACPClientProfile
from band.integrations.acp.permissions import (
    ElicitationHandler,
    PermissionHandler,
    cancel_permission,
    elicitation_session_id,
)
from band.integrations.acp.session_config import SessionConfigOption
from band.integrations.acp.stream import ACPChunkStream, ChunkSink
from band.integrations.acp.types import CollectedChunk
from band.integrations.acp.updates import ACPUpdateParser

logger = logging.getLogger(__name__)


class ACPCollectingClient(ACPUpdateParser, Client):  # type: ignore[misc]  # ACP Client has optional methods treated as abstract by pyrefly
    """Generic ACP client that buffers session updates by session_id.

    The ``acp`` transport runs each incoming notification as its own task, so
    consecutive ``session_update``s execute concurrently. A per-session lock
    serializes the ingest→sink path and the permission handler (which posts to
    the same room mid-turn): lock waiters wake FIFO and the tasks start in
    wire-arrival order, so room posts keep the stream's causal order.
    """

    def __init__(
        self,
        profile: ACPClientProfile | None = None,
        canonicalize_tool_name: Callable[[str], str] | None = None,
    ) -> None:
        self._profile = profile or NoopACPClientProfile()
        super().__init__(canonicalize_tool_name)
        self._stream = ACPChunkStream()
        self._permission_handlers: dict[str, PermissionHandler] = {}
        self._elicitation_handlers: dict[str, ElicitationHandler] = {}
        # Retain locks while a straggler can still hold one.
        self._session_locks: dict[str, asyncio.Lock] = {}
        # Each session's latest advertised config catalog; like the locks,
        # it outlives reset_session, which runs every turn.
        self._config_options: dict[str, tuple[SessionConfigOption, ...]] = {}

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        return self._session_locks.setdefault(session_id, asyncio.Lock())

    def _session_narrator(
        self, session_id: str
    ) -> Callable[[Awaitable[None]], Awaitable[None]]:
        """Serialize a narration awaitable under the session's chunk lock."""

        async def narrate(action: Awaitable[None]) -> None:
            async with self._session_lock(session_id):
                await self._stream.close_open_run(session_id)
                await action

        return narrate

    async def session_update(
        self, session_id: str, update: object, **kwargs: object
    ) -> None:
        del kwargs
        if isinstance(update, ConfigOptionUpdate):
            logger.debug("ACP session %s pushed new config options", session_id)
            self.record_config_options(session_id, update.config_options)
            return
        async with self._session_lock(session_id):
            chunk = self._chunk_from_update(update)
            if chunk is not None:
                await self._stream.ingest(session_id, chunk)

    async def request_permission(  # type: ignore[override]  # ACP Client uses specific types; we widen to object
        self,
        options: object,
        session_id: str,
        tool_call: object,
        **kwargs: object,
    ) -> dict[str, object]:
        handler = self._permission_handlers.get(session_id)
        if handler:
            # A manual handler can wait for room input. Holding the ingestion lock
            # for that wait stalls every live update, so the handler receives a
            # narrow narrator for the denied tool-call/tool-result pair instead.
            return await handler(
                options=options,
                session_id=session_id,
                tool_call=tool_call,
                narrate_permission=self._session_narrator(session_id),
                **kwargs,
            )

        logger.debug("Auto-cancelling permission request for session %s", session_id)
        return cancel_permission()

    async def create_elicitation(  # type: ignore[override]
        self,
        message: str,
        mode: object,
        **kwargs: object,
    ) -> object:
        # The modern ACP client API packs session scope into ``mode``
        # (``ElicitationFormSessionMode.session_id``); kwargs only carry
        # ``_meta``. Prefer the mode field so form handlers bind to the
        # same room session that registered them.
        session_id = elicitation_session_id(mode, kwargs)
        handler = self._elicitation_handlers.get(session_id)
        if handler is not None:
            return await handler(
                message=message,
                mode=mode,
                narrate_elicitation=self._session_narrator(session_id),
                **kwargs,
            )
        logger.debug(
            "Auto-declining elicitation for session %s (no handler)", session_id
        )
        return DeclineElicitationResponse(action="decline")

    def set_elicitation_handler(
        self,
        session_id: str,
        handler: ElicitationHandler | None,
    ) -> None:
        if handler is None:
            self._elicitation_handlers.pop(session_id, None)
        else:
            self._elicitation_handlers[session_id] = handler

    def set_permission_handler(
        self,
        session_id: str,
        handler: PermissionHandler | None,
    ) -> None:
        if handler is None:
            self._permission_handlers.pop(session_id, None)
        else:
            self._permission_handlers[session_id] = handler

    def record_config_options(
        self, session_id: str, options: Sequence[SessionConfigOption]
    ) -> None:
        self._config_options[session_id] = tuple(options)

    def config_options(self, session_id: str) -> tuple[SessionConfigOption, ...]:
        return self._config_options.get(session_id, ())

    def forget_config_options(self, session_id: str) -> None:
        self._config_options.pop(session_id, None)

    def reset_session(self, session_id: str) -> None:
        self._stream.reset_session(session_id)
        self._permission_handlers.pop(session_id, None)
        self._elicitation_handlers.pop(session_id, None)

    def set_sink(self, session_id: str, sink: ChunkSink | None) -> None:
        self._stream.set_sink(session_id, sink)

    async def flush(self, session_id: str) -> None:
        async with self._session_lock(session_id):
            await self._stream.flush(session_id)

    def get_collected_text(self, session_id: str | None = None) -> str:
        return self._stream.get_collected_text(session_id)

    def get_collected_chunks(
        self, session_id: str | None = None
    ) -> list[CollectedChunk]:
        return self._stream.get_collected_chunks(session_id)

    async def ext_method(
        self,
        method: str,
        params: dict[str, object],
    ) -> dict[str, object]:
        return await self._profile.ext_method(method, params)

    async def ext_notification(self, method: str, params: dict[str, object]) -> None:
        session_id = str(params.get("sessionId") or params.get("session_id") or "")
        if not session_id:
            # A profile written against the pre-extension_session_id
            # ACPClientProfile protocol has no such attribute at all.
            session_id = getattr(self._profile, "extension_session_id", None) or ""
        if not session_id:
            return

        chunks = await self._profile.ext_notification(method, params)
        if chunks:
            async with self._session_lock(session_id):
                await self._stream.close_open_run(session_id)
                for chunk in chunks:
                    await self._stream.finalize(session_id, chunk)
