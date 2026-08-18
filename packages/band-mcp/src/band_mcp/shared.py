"""Shared resolver, logger, and settings for band-mcp.

``build_standalone_resolver(config)`` constructs everything synchronously,
before the FastMCP instance is even built (``server.py`` calls it, then
``build_engine(standalone_spec(config))``). The engine's registrations
capture this resolver directly in a closure, so no FastMCP-injected
``Context`` parameter or lifespan machinery is needed to reach it.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import Any

from band_rest import AsyncRestClient
from band.config.logs import LogSettings
from band.integrations.mcp.engine import dispatch_tool
from band.logging_config import LogStream
from band.runtime.tools import (
    SEND_MESSAGE_TOOL_NAME,
    AgentTools,
    HumanTools,
    Surface,
    ToolDefinition,
    TOOL_DEFINITIONS,
)

from band_mcp.config import Config, Scope, resolve_credential_for_scope, settings

SEND_MESSAGE_METHOD_NAME = TOOL_DEFINITIONS[SEND_MESSAGE_TOOL_NAME].method_name
"""The one thing that needs to stay in sync with :func:`_invoke_agent`'s
pre-flight participant refresh below -- read off the registry directly so a
future rename can't silently drift out of sync with a hand-typed sibling."""

# The stream is not negotiable: stdio transport's stdout is the JSON-RPC
# channel, so a log line written there corrupts the session (matches
# band-acp's cli.py, which pins the same override for the same reason).
# for_application(): band-mcp's own logger (band_mcp.*) is not a child of the
# "band" logger LogSettings raises by default, so without this the process's
# own startup/warning logs would be silently suppressed below BAND_LOG_LEVEL.
LogSettings(log_stream=LogStream.STDERR).for_application().configure()
logger = logging.getLogger(__name__)

AGENT_TOOLS_CACHE_MAX_SIZE = 128
# Matches the cache size: a coarser stripe count lets two unrelated chat_ids
# share a lock, so one room's in-flight REST call (the send_message
# participant refresh below) can block an unrelated room's call for no
# reason. One stripe per possible cache entry removes that false contention.
AGENT_TOOLS_LOCK_STRIPES = AGENT_TOOLS_CACHE_MAX_SIZE


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
        self._agent_id: str | None = None
        self._agent_id_resolved = False
        self._agent_id_lock = asyncio.Lock()
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
        return await dispatch_tool(self._human_tools, definition, arguments)

    async def _invoke_agent(
        self,
        definition: ToolDefinition,
        chat_id: str | None,
        arguments: dict[str, Any],
    ) -> Any:
        async with self._agent_tools_lock(chat_id):
            tools = await self._get_or_create_agent_tools(chat_id, definition.name)
            if definition.method_name == SEND_MESSAGE_METHOD_NAME:
                try:
                    refreshed = tools.get_participants()
                    if asyncio.iscoroutine(refreshed):
                        await refreshed
                except Exception:
                    self._discard_agent_tools(chat_id, tools)
                    raise

            return await dispatch_tool(tools, definition, arguments)

    async def _resolve_agent_id(self) -> str | None:
        """This agent's own id, resolved once and cached for the resolver's lifetime.

        Threaded into every :class:`AgentTools` instance below so
        ``available_mention_handles()`` can exclude the agent's own
        participant entry from a failed ``send_message``'s mention hint --
        the same exclusion the embedded door gets for free via
        ``AgentTools.from_context(ctx)``.
        """
        if self._agent_id_resolved:
            return self._agent_id
        async with self._agent_id_lock:
            # Re-check: a concurrent caller (different chat_id stripe) may
            # have already resolved it while this one waited for the lock.
            if self._agent_id_resolved:
                return self._agent_id
            assert self._agent_rest is not None
            identity = await self._agent_rest.agent_api_identity.get_agent_me()
            self._agent_id = identity.data.id
            self._agent_id_resolved = True
            return self._agent_id

    async def _get_or_create_agent_tools(
        self, chat_id: str | None, tool_name: str
    ) -> AgentTools:
        cached = self._agent_tools_cache.get(chat_id)
        if cached is not None:
            self._agent_tools_cache.move_to_end(chat_id)
            return cached

        if self._agent_rest is None:
            raise RuntimeError(
                f"{tool_name}: agent tools not available "
                "(no agent credential configured)"
            )

        agent_id = await self._resolve_agent_id()
        # Room-less agent tools (chat_id is None) still need a string for the
        # SDK constructor -- "" is the sentinel, matching the None cache key.
        instance = AgentTools(
            room_id=chat_id if chat_id is not None else "",
            rest=self._agent_rest,
            agent_id=agent_id,
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
