"""Shared resolver, logger, and settings for band-mcp.

INT-1096: this module used to build an ``AppContext`` threaded through
FastMCP's lifespan/``Context`` machinery (``app_lifespan``,
``set_pending_config``, ``get_app_context``) because the old registrar
needed a way to reach per-room state from inside a FastMCP-injected
``Context`` parameter. The new engine's registrations capture their
resolver directly in a closure instead (see
``band.integrations.mcp.engine.build_tool_registration``), so none of that
indirection is needed any more: ``build_standalone_resolver(config)``
constructs everything synchronously, before the FastMCP instance is even
built (``server.py`` calls it, then ``build_engine(standalone_spec(config))``).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections import OrderedDict
from typing import Any

from band_rest import AsyncRestClient
from band.core.exceptions import BandToolError
from band.integrations.mcp.engine import enrich_send_message_error
from band.runtime.tools import AgentTools, HumanTools, Surface, ToolDefinition

from band_mcp.config import Config, Scope, resolve_credential_for_scope, settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

AGENT_TOOLS_CACHE_MAX_SIZE = 128
AGENT_TOOLS_LOCK_STRIPES = 64


class StandaloneResolver:
    """The CLI door's :class:`ToolsResolver` (divergence-matrix rows 9, 11, 24).

    Owns everything the old ``AppContext``/module-level cache functions did,
    now as one self-contained object instead of free functions reading a
    FastMCP ``Context``:

    - Human surface: dispatches straight to the startup-constructed
      ``HumanTools`` singleton (stateless per credential, so no locking).
    - Agent surface: per-room ``AgentTools`` instances, LRU-cached (128) with
      64 lock stripes serializing calls that may share a mutable instance.
      Room-less agent tools use ``None`` as the cache key and pass the SDK
      constructor an ``""`` sentinel, so they never share participant state
      with a room-scoped instance. ``band_send_message`` gets a pre-flight
      participant refresh, discarding the cached instance on failure.
    """

    def __init__(
        self,
        *,
        human_tools: Any = None,
        agent_rest: AsyncRestClient | None = None,
    ) -> None:
        self._human_tools = human_tools
        self._agent_rest = agent_rest
        self._agent_tools_cache: OrderedDict[str | None, Any] = OrderedDict()
        self._agent_tools_locks: list[asyncio.Lock] = [
            asyncio.Lock() for _ in range(AGENT_TOOLS_LOCK_STRIPES)
        ]

    @property
    def human_rest(self) -> AsyncRestClient | None:
        return getattr(self._human_tools, "rest", None)

    @property
    def agent_rest(self) -> AsyncRestClient | None:
        return self._agent_rest

    async def invoke(
        self,
        definition: ToolDefinition,
        chat_id: str | None,
        arguments: dict[str, Any],
    ) -> Any:
        if definition.surface == Surface.HUMAN:
            return await self._invoke_human(definition, arguments)
        return await self._invoke_agent(definition, chat_id, arguments)

    async def _invoke_human(
        self, definition: ToolDefinition, arguments: dict[str, Any]
    ) -> Any:
        if self._human_tools is None:
            logger.warning(
                "%s: human tools not available (no user credential configured).",
                definition.name,
            )
            raise RuntimeError(f"{definition.name}: human tools not available")
        method = getattr(self._human_tools, definition.method_name)
        return await method(**arguments)

    async def _invoke_agent(
        self,
        definition: ToolDefinition,
        chat_id: str | None,
        arguments: dict[str, Any],
    ) -> Any:
        async with self._agent_tools_lock(chat_id):
            tools = self._get_or_create_agent_tools(chat_id)
            if definition.method_name == "send_message":
                try:
                    refreshed = tools.get_participants()
                    if asyncio.iscoroutine(refreshed):
                        await refreshed
                except Exception:
                    self._discard_agent_tools(chat_id, tools)
                    raise

            method = getattr(tools, definition.method_name)
            try:
                return await method(**arguments)
            except (ValueError, BandToolError) as error:
                raise enrich_send_message_error(definition, tools, error) from error

    def _get_or_create_agent_tools(self, chat_id: str | None) -> AgentTools:
        cached = self._agent_tools_cache.get(chat_id)
        if cached is not None:
            self._agent_tools_cache.move_to_end(chat_id)
            return cached

        if self._agent_rest is None:
            raise RuntimeError(
                "agent tools not available (no agent credential configured)"
            )

        # Room-less agent tools (chat_id is None) still need a string for the
        # SDK constructor -- "" is the sentinel, matching the None cache key.
        instance = AgentTools(
            room_id=chat_id if chat_id is not None else "", rest=self._agent_rest
        )
        self._agent_tools_cache[chat_id] = instance
        self._agent_tools_cache.move_to_end(chat_id)
        while len(self._agent_tools_cache) > AGENT_TOOLS_CACHE_MAX_SIZE:
            self._agent_tools_cache.popitem(last=False)
        return instance

    def _discard_agent_tools(self, chat_id: str | None, instance: Any) -> None:
        if self._agent_tools_cache.get(chat_id) is instance:
            self._agent_tools_cache.pop(chat_id, None)

    def _agent_tools_lock(self, chat_id: str | None) -> asyncio.Lock:
        """The fixed lock stripe protecting a cached ``AgentTools`` instance."""
        return self._agent_tools_locks[hash(chat_id) % len(self._agent_tools_locks)]


def build_standalone_resolver(config: Config) -> StandaloneResolver:
    """Build a :class:`StandaloneResolver` from a resolved :class:`Config`.

    REST clients are constructed eagerly and synchronously here -- client
    construction does no I/O, so there's no need for FastMCP's lifespan to
    defer it. A client is only built for a scope that actually resolves to a
    credential, so a human-only or agent-only deployment doesn't open a
    connection it will never use.
    """
    base_url = settings.band_base_url

    human_tools: Any = None
    if Scope.HUMAN in config.scope:
        human_cred = resolve_credential_for_scope(config, Scope.HUMAN)
        if human_cred is not None:
            human_rest = AsyncRestClient(api_key=human_cred, base_url=base_url)
            human_tools = HumanTools(rest=human_rest)

    agent_rest: AsyncRestClient | None = None
    if Scope.AGENT in config.scope:
        agent_cred = resolve_credential_for_scope(config, Scope.AGENT)
        if agent_cred is not None:
            agent_rest = AsyncRestClient(api_key=agent_cred, base_url=base_url)

    return StandaloneResolver(human_tools=human_tools, agent_rest=agent_rest)
