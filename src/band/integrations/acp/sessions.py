"""Session operations on a connected ACP agent."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import cast

from acp import text_block
from acp.exceptions import RequestError
from acp.schema import (
    LoadSessionResponse,
    NewSessionResponse,
    SetSessionConfigOptionResponse,
)

from band.integrations.acp.collecting import ACPCollectingClient
from band.integrations.acp.permissions import ElicitationHandler, PermissionHandler
from band.integrations.acp.session_config import (
    SessionConfigOption,
    session_config_options,
)
from band.integrations.acp.stream import ChunkSink
from band.integrations.acp.transport import ACPConnectionProtocol
from band.integrations.acp.types import CollectedChunk
from band.integrations.mcp.backends import BandMCPTransport

logger = logging.getLogger(__name__)
ACP_SESSION_LOAD_TIMEOUT_SECONDS = 5.0


class ACPSessionOperations(ABC):
    """Control sessions through a lifecycle-owned connection."""

    def __init__(self) -> None:
        self._client: ACPCollectingClient | None = None
        self._agent_mcp_transport = BandMCPTransport.HTTP
        self._agent_supports_session_load = False
        self._agent_supports_session_close = False
        self._config_lock = asyncio.Lock()

    @abstractmethod
    async def ensure_connection(self, *, can_respawn: bool) -> ACPConnectionProtocol:
        """Return the lifecycle owner's active connection."""
        raise NotImplementedError

    async def create_session(self, *, cwd: str, mcp_servers: list[object]) -> str:
        session = await self.create_session_response(cwd=cwd, mcp_servers=mcp_servers)
        return session.session_id

    async def create_session_response(
        self, *, cwd: str, mcp_servers: list[object]
    ) -> NewSessionResponse:
        conn = await self.ensure_connection(can_respawn=False)
        response = cast(
            NewSessionResponse,
            await conn.new_session(cwd=cwd, mcp_servers=mcp_servers),
        )
        self._record_config_options(response.session_id, response)
        return response

    async def load_session(
        self,
        *,
        cwd: str,
        session_id: str,
        mcp_servers: list[object],
    ) -> bool:
        return (
            await self.load_session_response(
                cwd=cwd,
                session_id=session_id,
                mcp_servers=mcp_servers,
            )
        ) is not None

    async def load_session_response(
        self,
        *,
        cwd: str,
        session_id: str,
        mcp_servers: list[object],
    ) -> LoadSessionResponse | None:
        """Load a persisted session, falling back on an unavailable or failed load."""
        if not self._agent_supports_session_load:
            return None

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
            return None
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
            return None
        self._record_config_options(session_id, response)
        return response

    async def set_config_option(
        self,
        *,
        session_id: str,
        config_id: str,
        value: str,
    ) -> SetSessionConfigOptionResponse | None:
        """Set one advertised select option and return the refreshed catalog."""
        conn = await self.ensure_connection(can_respawn=False)
        response = await conn.set_config_option(
            session_id=session_id,
            config_id=config_id,
            value=value,
        )
        self._record_config_options(session_id, response)
        return response

    def config_options(self, session_id: str) -> tuple[SessionConfigOption, ...]:
        """The session's live catalog: its setup response, then every change."""
        if self._client is None:
            return ()
        return self._client.config_options(session_id)

    @property
    def config_lock(self) -> asyncio.Lock:
        """Held across a runtime switch."""
        return self._config_lock

    def _record_config_options(self, session_id: str, response: object) -> None:
        options = session_config_options(response)
        if options is not None and self._client is not None:
            self._client.record_config_options(session_id, options)

    async def close_session(self, session_id: str) -> None:
        """Close a session when the agent advertised lifecycle support."""
        if self._client is not None:
            self._client.forget_config_options(session_id)
        if not self._agent_supports_session_close:
            return
        conn = await self.ensure_connection(can_respawn=False)
        await conn.close_session(session_id)

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

    async def cancel_turn(self, session_id: str) -> None:
        """Tell the agent to stop a room's in-flight prompt."""
        conn = await self.ensure_connection(can_respawn=False)
        await conn.cancel(session_id)

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

    def set_elicitation_handler(
        self,
        session_id: str,
        handler: ElicitationHandler | None,
    ) -> None:
        if self._client is not None:
            self._client.set_elicitation_handler(session_id, handler)

    def get_collected_chunks(self, session_id: str) -> list[CollectedChunk]:
        if self._client is None:
            return []
        return self._client.get_collected_chunks(session_id)

    @property
    def client(self) -> ACPCollectingClient | None:
        """The active client for operations beyond the runtime session interface."""
        return self._client

    @property
    def agent_mcp_transport(self) -> BandMCPTransport:
        """The MCP transport the connected agent negotiated during ``start()``."""
        return self._agent_mcp_transport

    @staticmethod
    def _is_missing_session_error(error: RequestError) -> bool:
        """Whether an ACP ``session/load`` failure means the session is absent."""
        return error.code == -32002 or (
            "session" in str(error).lower() and "not found" in str(error).lower()
        )
