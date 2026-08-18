"""Shared app context, logger, and FastMCP singleton for band-mcp.

`AppContext` carries two async REST clients: `human_rest` (bound to
`user_key` or a human-capable legacy key) and `agent_rest` (bound to
`agent_key` or an agent-capable legacy key). Either may be None when the
corresponding scope is not served by the current config.

HumanTools / AgentTools coordination with the SDK
-------------------------------------------------
The SDK's `HumanTools` and `AgentTools` classes are provided by the `band-sdk`
package. `get_human_tools()` / `get_agent_tools()` use startup-validated SDK classes;
missing SDK imports raise `ConfigError` because the SDK is a hard dependency.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession
from mcp.server.transport_security import TransportSecuritySettings
from band_rest import AsyncRestClient

from band_mcp.config import (
    Config,
    ConfigError,
    _legacy_key_capabilities,
    settings,
    resolve_credential_for_scope,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

AGENT_TOOLS_CACHE_MAX_SIZE = 128
AGENT_TOOLS_LOCK_STRIPES = 64


@dataclass
class AppContext:
    """Type-safe container for application dependencies.

    `human_rest` / `agent_rest` are async REST clients used by the registrar.
    Either may be None when the corresponding scope is not served by the
    current config (e.g. a human-only deployment has no `agent_rest`).

    `human_tools` is the startup-constructed singleton returned by
    `get_human_tools()`. `AgentTools` is constructed per-room and cached in
    `_agent_tools_cache` by `get_agent_tools()`.

    `pinned_room_id`, `scope`, and `tools` carry the resolved Config values
    forward so the registrar doesn't need to re-resolve.
    """

    human_rest: AsyncRestClient | None = None
    agent_rest: AsyncRestClient | None = None
    human_tools: Any = None  # HumanTools | None; typed Any to avoid SDK hard-dep
    pinned_room_id: str | None = None
    scope: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)

    # Lifespan cache for AgentTools keyed by room_id. Room-less agent tools use
    # None as the cache key. Cached room instances preserve SDK participant
    # state across sequential MCP tool calls in the same server process.
    _agent_tools_cache: OrderedDict[str | None, Any] = field(
        default_factory=OrderedDict
    )

    # Fixed lock stripes serialize calls that may share a mutable AgentTools
    # instance without letting caller-controlled room ids grow lock storage.
    _agent_tools_locks: list[asyncio.Lock] = field(
        default_factory=lambda: [
            asyncio.Lock() for _ in range(AGENT_TOOLS_LOCK_STRIPES)
        ]
    )


AppContextType = Context[ServerSession, AppContext, None]


def _require_sdk_tools() -> tuple[Any, Any]:
    """Import and return ``(HumanTools, AgentTools)`` from the SDK.

    Raises ``ConfigError`` if the SDK package is not importable, so the
    operator gets a clear startup error instead of a silent empty tool
    surface. ``band-sdk`` is a hard dependency, so a missing import means
    the install is broken.
    """
    try:
        from band.runtime.tools import AgentTools, HumanTools
    except ImportError as exc:
        raise ConfigError(
            "band-sdk is required but is not importable "
            "(`from band.runtime.tools import HumanTools, AgentTools` "
            f"failed: {exc}). Install/upgrade with "
            "`pip install 'band-sdk>=1.0.0'` or `uv sync`."
        ) from exc
    return HumanTools, AgentTools


def _try_import_human_tools() -> Any:
    """Return SDK ``HumanTools`` class. Raises ConfigError if unavailable."""
    HumanTools, _ = _require_sdk_tools()
    return HumanTools


def _try_import_agent_tools() -> Any:
    """Return SDK ``AgentTools`` class. Raises ConfigError if unavailable."""
    _, AgentTools = _require_sdk_tools()
    return AgentTools


def build_app_context(
    config: Config | None = None,
) -> AppContext:
    """Construct an `AppContext` from a resolved `Config`.

    Per-scope `AsyncRestClient` instances are built lazily: a client is only
    constructed for a scope that resolves to a credential. This keeps
    human-only or agent-only deployments from opening connections they'll
    never use.

    If `config` is None, we fall back to the legacy `BAND_API_KEY` path:
    the async slots are populated from the single legacy key only for scopes its
    prefix can serve. If `settings.band_api_key` is unset, the AppContext is
    returned with both slots None — tool calls will fail at request time with a
    structured error.
    """
    base_url = settings.band_base_url

    if config is None:
        # Legacy path with no resolved Config. Build clients only for scopes the
        # legacy key prefix can serve (e.g. thnv_u_* cannot serve agent calls).
        legacy_key = settings.band_api_key or ""
        legacy_human, legacy_agent = _legacy_key_capabilities(legacy_key)
        human_rest = (
            AsyncRestClient(api_key=legacy_key, base_url=base_url)
            if legacy_key and legacy_human
            else None
        )
        agent_rest = (
            AsyncRestClient(api_key=legacy_key, base_url=base_url)
            if legacy_key and legacy_agent
            else None
        )
        return AppContext(human_rest=human_rest, agent_rest=agent_rest)

    human_rest: AsyncRestClient | None = None
    agent_rest: AsyncRestClient | None = None

    human_cred = (
        resolve_credential_for_scope(config, "human")
        if "human" in config.scope
        else None
    )
    agent_cred = (
        resolve_credential_for_scope(config, "agent")
        if "agent" in config.scope
        else None
    )

    if human_cred is not None:
        human_rest = AsyncRestClient(api_key=human_cred, base_url=base_url)
    if agent_cred is not None:
        agent_rest = AsyncRestClient(api_key=agent_cred, base_url=base_url)

    # Startup-construct `HumanTools` singleton if the human client is
    # available. AgentTools is per-room and constructed on demand.
    # `_try_import_human_tools` raises ConfigError if the SDK is missing —
    # we let that propagate so the operator sees a clear startup failure
    # instead of a running-but-empty MCP server.
    human_tools_obj: Any = None
    if human_rest is not None:
        HumanToolsCls = _try_import_human_tools()
        try:
            human_tools_obj = HumanToolsCls(rest=human_rest)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to construct HumanTools singleton: %s", exc)
            human_tools_obj = None

    return AppContext(
        human_rest=human_rest,
        agent_rest=agent_rest,
        human_tools=human_tools_obj,
        pinned_room_id=config.room_id,
        scope=list(config.scope),
        tools=list(config.tools),
    )


# Module-level slot the lifespan reads; server.run() populates this before
# starting FastMCP. Using a module-level value (vs passing through closures)
# matches how `settings` is already consumed and keeps the lifespan signature
# unchanged.
_pending_config: Config | None = None


def set_pending_config(config: Config) -> None:
    """Store the resolved config for the lifespan to pick up at startup."""
    global _pending_config
    _pending_config = config


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Lifespan context manager for MCP server."""
    logger.info("Initializing Band API client")
    app_context = build_app_context(_pending_config)
    logger.info("Band MCP server lifespan started successfully")

    try:
        yield app_context
    finally:
        logger.info("Band MCP server lifespan shutdown complete")


def get_app_context(ctx: AppContextType) -> AppContext:
    """Helper to extract AppContext from the lifespan context.

    Usage in tools:
        app_ctx = get_app_context(ctx)
        human_rest = app_ctx.human_rest  # async REST client for human scope
        agent_rest = app_ctx.agent_rest  # async REST client for agent scope
    """
    return ctx.request_context.lifespan_context


def get_human_tools(ctx: AppContextType) -> Any:
    """Return the startup-constructed `HumanTools` singleton, or None.

    This is called per tool invocation. The singleton is built
    once in `build_app_context` from the human `AsyncRestClient`; there is no
    per-request reconstruction.

    Returns None when the deployment has no human credential. Missing SDK
    imports raise ConfigError before the server advertises tools.
    """
    app_ctx = get_app_context(ctx)
    if app_ctx.human_tools is None:
        logger.warning(
            "get_human_tools(): HumanTools not available. Ensure a human "
            "credential is configured for the human scope."
        )
    return app_ctx.human_tools


def get_agent_tools(
    ctx: AppContextType,
    room_id: str | None,
    *,
    sdk_room_id: str | None = None,
) -> Any:
    """Return an `AgentTools` instance scoped to `room_id`.

    Lifespan cache: repeated calls for the same room return the same SDK
    `AgentTools` instance for as long as the MCP server process is alive. This
    preserves SDK-side participant state across sequential MCP calls. Room-less
    agent tools use None as the cache key and can pass a string sentinel via
    `sdk_room_id` to satisfy the SDK constructor contract.

    Returns None when no agent credential is configured. Raises
    ``ConfigError`` (via ``_try_import_agent_tools``) when the SDK is not
    installed — that condition should have been caught at startup but this
    keeps us honest if a tool is dispatched on a broken install.
    """
    app_ctx = get_app_context(ctx)
    if app_ctx.agent_rest is None:
        logger.warning(
            "get_agent_tools(room_id=%s): no agent credential configured.",
            room_id,
        )
        return None

    cached = app_ctx._agent_tools_cache.get(room_id)
    if cached is not None:
        app_ctx._agent_tools_cache.move_to_end(room_id)
        return cached

    AgentToolsCls = _try_import_agent_tools()

    try:
        instance = AgentToolsCls(
            room_id=room_id if sdk_room_id is None else sdk_room_id,
            rest=app_ctx.agent_rest,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to construct AgentTools for room %s: %s", room_id, exc)
        return None

    app_ctx._agent_tools_cache[room_id] = instance
    app_ctx._agent_tools_cache.move_to_end(room_id)
    while len(app_ctx._agent_tools_cache) > AGENT_TOOLS_CACHE_MAX_SIZE:
        app_ctx._agent_tools_cache.popitem(last=False)
    return instance


def discard_agent_tools(
    ctx: AppContextType, room_id: str | None, instance: Any
) -> None:
    """Drop a cached `AgentTools` instance if it is still current."""
    app_ctx = get_app_context(ctx)
    if app_ctx._agent_tools_cache.get(room_id) is instance:
        app_ctx._agent_tools_cache.pop(room_id, None)


def get_agent_tools_lock(ctx: AppContextType, room_id: str | None) -> asyncio.Lock:
    """Return the lock stripe protecting a cached `AgentTools` instance."""
    app_ctx = get_app_context(ctx)
    return app_ctx._agent_tools_locks[hash(room_id) % len(app_ctx._agent_tools_locks)]


transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=settings.enable_dns_rebinding_protection,
    allowed_hosts=settings.allowed_hosts,
    allowed_origins=settings.allowed_origins,
)

if (
    settings.transport == "sse"
    and settings.enable_dns_rebinding_protection
    and not settings.allowed_hosts
):
    logger.warning(
        "DNS rebinding protection enabled with empty ALLOWED_HOSTS. "
        "All SSE requests will be blocked. Configure ALLOWED_HOSTS to allow connections."
    )

mcp = FastMCP(
    name="band-mcp-server",
    lifespan=app_lifespan,
    host=settings.host,
    port=settings.port,
    transport_security=transport_security,
)
