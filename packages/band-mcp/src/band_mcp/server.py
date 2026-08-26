"""MCP server entry point.

Dual-credential configuration: `--user-key`, `--agent-key`,
`--room-id`, `--scope`, `--tools` CLI flags (plus matching env vars). Tool
registration builds an ``EngineSpec`` (``standalone_spec``, below) and hands
it to the shared engine (``band.integrations.mcp.engine.build_engine``).
There is no single-key fallback -- a credential is either scope-specific or
absent.

CLI parsing is Typer, not argparse: it's already a real dependency here
(``mcp[cli]`` requires it), so this adds no new install footprint. Typer
only replaces the parsing/choice-validation/help-text layer -- all real
config validation and CLI>env precedence still runs through
``resolve_config``/``validate`` in ``config.py``, a pure, framework-agnostic
pair kept deliberately independent of whichever CLI library calls them.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, NoReturn

import typer
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from band.integrations.mcp.engine import (
    EngineSpec,
    SendEventWideInput,
    build_engine,
    build_tool_registration,
    extend_with_chat_id,
    pin_existing_chat_id,
)
from band.core.types import Capability
from band.runtime.tools import (
    EVENT_TOOL_NAMES,
    Surface,
    classify_room_binding,
    iter_tool_definitions,
)

from band_mcp import __version__
from band_mcp.config import (
    DEFAULT_SCOPE,
    DEFAULT_TOOLS,
    VALID_SCOPES,
    VALID_TOOLS,
    CliArgs,
    Config,
    ConfigError,
    Transport,
    resolve_config,
    settings,
    validate,
)
from band_mcp.shared import StandaloneResolver, build_standalone_resolver, logger


def standalone_spec(config: Config, resolver: StandaloneResolver) -> EngineSpec:
    """Build the CLI door's :class:`EngineSpec` from a resolved :class:`Config`.

    ``resolver`` is a caller-supplied dependency, not built here: ``run()``
    keeps its own reference to wire ``health_check`` to the same
    ``human_rest``/``agent_rest`` this spec's registrations dispatch through.

    Per-tool classification (divergence-matrix row 2): unlike the embedded
    door's uniform wrap, the CLI advertises a room field only on the tools
    that actually need one (``classify_room_binding`` -- the published
    band-mcp 1.3.2 contract). ``band_send_event`` additionally widens to
    ``SendEventWideInput`` (row 6): a standalone agent has no adapter
    narrating tool_call/tool_result for it.
    """
    # band-mcp's `--tools` vocabulary (`ToolGroup`) is CLI-facing (shell
    # flags) and genuinely distinct from the SDK's `Capability` enum -- not
    # merged into it -- but every group band-mcp exposes today shares its
    # string value with the matching capability, so converting by value
    # needs no maintained mapping and fails loudly (`ValueError`) the day a
    # group's value stops lining up, instead of silently registering no
    # tools for it. `ToolGroup` has no `FILES` member yet: this CLI's tool
    # list is built synchronously at startup, before any feature-flag
    # negotiation against the platform is possible. Add an explicit mapping
    # only once a CLI group genuinely differs from, or maps to more than
    # one, SDK capability.
    capabilities: frozenset[Capability] = frozenset(
        Capability(group) for group in config.tools
    )
    pinned_room_id = config.room_id

    registrations = []
    seen_names: dict[str, str] = {}
    for surface in config.scope:
        for definition in iter_tool_definitions(
            # Deliberately crossing into `runtime.tools`'s own `Surface`
            # vocabulary here: it happens to share `Scope`'s two string
            # values today, but the two are conceptually distinct closed
            # vocabularies, so the boundary is converted explicitly rather
            # than merged.
            surface=Surface(surface),
            capabilities=capabilities,
        ):
            previous_surface = seen_names.get(definition.name)
            if previous_surface is not None:
                raise ConfigError(
                    "Duplicate tool name across enabled surfaces: "
                    f"{definition.name} ({previous_surface}, {definition.surface})"
                )
            seen_names[definition.name] = definition.surface

            is_agent_room_bound, is_human_room_bound = classify_room_binding(definition)
            room_bound = is_agent_room_bound or is_human_room_bound

            model = definition.input_model
            if definition.name in EVENT_TOOL_NAMES:
                model = SendEventWideInput
            if is_agent_room_bound:
                model = extend_with_chat_id(model, pinned_room_id)
            elif is_human_room_bound and pinned_room_id is not None:
                model = pin_existing_chat_id(model)

            registrations.append(
                build_tool_registration(
                    definition,
                    model,
                    resolver=resolver,
                    strip_chat_id=is_agent_room_bound,
                    pinned_room_id=pinned_room_id if room_bound else None,
                )
            )

    return EngineSpec(name="band-mcp-server", tools=tuple(registrations))


async def _probe_surface(
    name: str, call: Callable[[], Awaitable[Any]]
) -> tuple[str, Exception | None]:
    try:
        await call()
        return name, None
    except Exception as exc:  # noqa: BLE001 - surfaced as this probe's own result
        return name, exc


async def _health_check(resolver: StandaloneResolver) -> str:
    """Test MCP server and API connectivity.

    A module-level function taking ``resolver`` explicitly (rather than a
    bare ``@mcp.tool()`` closure) so it stays unit-testable in isolation --
    ``run()`` registers a zero-arg wrapper that closes over the real resolver.
    Human and agent connectivity hit independent credentials/endpoints, so
    they run concurrently; the first *configured* surface's failure (human
    before agent) still wins the returned message, matching the sequential
    version's precedence.
    """
    probes: list[tuple[str, Callable[[], Awaitable[Any]]]] = []
    if resolver.human_rest is not None:
        probes.append(("human", resolver.human_rest.human_api_agents.list_my_agents))
    if resolver.agent_rest is not None:
        probes.append(("agent", resolver.agent_rest.agent_api_identity.get_agent_me))

    results = await asyncio.gather(
        *(_probe_surface(name, call) for name, call in probes)
    )
    for name, exc in results:
        if exc is not None:
            return f"Failed | {name} | {exc}"

    checked = [name for name, _ in results]
    if checked:
        return f"OK | {','.join(checked)} | {settings.band_base_url}"
    return "Failed | no credential configured"


def _build_transport_security(transport: Transport) -> TransportSecuritySettings:
    if (
        transport == Transport.SSE
        and settings.enable_dns_rebinding_protection
        and not settings.allowed_hosts
    ):
        logger.warning(
            "DNS rebinding protection enabled with empty ALLOWED_HOSTS. "
            "All SSE requests will be blocked. Configure ALLOWED_HOSTS to allow connections."
        )
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=settings.enable_dns_rebinding_protection,
        allowed_hosts=settings.allowed_hosts,
        allowed_origins=settings.allowed_origins,
    )


def _register_health_check_tool(mcp: FastMCP, resolver: StandaloneResolver) -> None:
    # Named health_check directly (not e.g. _health_check_tool): FastMCP
    # derives the advertised schema's "title" from the function's own
    # __name__, independent of the tool() name= override below -- a wrapper
    # named differently would leak into the wire-visible schema title.
    @mcp.tool(name="health_check")
    async def health_check() -> str:
        """Test MCP server and API connectivity."""
        return await _health_check(resolver)


def _exit_on_config_error(exc: ConfigError) -> NoReturn:
    logger.error("Configuration error: %s", exc)
    raise typer.Exit(2) from exc


def _resolve_validated_config(cli: CliArgs) -> Config:
    """Resolve Config from CLI+env, emit its warnings, and validate it.

    Warnings are emitted before validate() so an operator sees "did you
    mean" hints even when validation also fails -- did-you-mean first,
    credentials-missing last.
    """
    config = resolve_config(cli=cli, env=os.environ)
    for warning in config.warnings:
        logger.warning(warning.message)
    validate(config)
    return config


def _run_transport(
    mcp: FastMCP, transport: Transport, host: str | None, port: int | None
) -> None:
    match transport:
        case Transport.STDIO:
            logger.info("Transport: STDIO (for IDE integration)")
            logger.info("Server ready - listening for MCP protocol messages on STDIO")
            mcp.run(transport="stdio")
        case Transport.SSE:
            sse_host = host or settings.host
            sse_port = port or settings.port
            logger.info("Transport: SSE (HTTP server mode)")
            logger.info("Server ready - listening on http://%s:%s", sse_host, sse_port)
            logger.info("SSE endpoint: /sse | Messages endpoint: /messages/")
            mcp.run(transport="sse")


# Derived from config.py's Scope/ToolGroup vocabulary and their defaults
# rather than retyped here, so a future scope/tool addition can't leave
# --help advertising a stale, incomplete value/default list.
_SCOPE_VALUES = ", ".join(VALID_SCOPES)
_SCOPE_DEFAULT = ", ".join(DEFAULT_SCOPE) or "none"
_TOOLS_VALUES = ", ".join(VALID_TOOLS)
_TOOLS_DEFAULT = ", ".join(DEFAULT_TOOLS) or "none"

_EPILOG = f"""
Transport Modes:
  stdio   Default mode for IDE integration (Cursor, Claude Desktop, etc.)
          Communication via standard input/output streams.

  sse     HTTP server mode for remote/Docker deployments.
          Runs as a persistent HTTP service with Server-Sent Events.

Examples:
  band-mcp                                 # Run with STDIO (default)
  band-mcp --transport sse                 # Run as HTTP server on 127.0.0.1:8000
  band-mcp --scope agent,human             # Serve both scopes
  band-mcp --scope agent --tools contacts  # Agent + opt-in contacts tools
  band-mcp --scope agent --room-id r_123   # Pin to a single room

Environment Variables:
  BAND_USER_KEY         User (human scope) API key
  BAND_AGENT_KEY        Agent scope API key
  BAND_MCP_SCOPE        Comma-separated scopes (default: {_SCOPE_DEFAULT})
  BAND_MCP_TOOLS        Opt-in tool groups: {_TOOLS_VALUES}
  BAND_MCP_ROOM_ID      Optional pinned room id
  BAND_BASE_URL         Base URL for Band API (default: https://app.band.ai)
  TRANSPORT             Transport mode: stdio or sse (default: stdio)
  HOST                  Host to bind for SSE mode (default: 127.0.0.1)
  PORT                  Port to bind for SSE mode (default: 8000)
"""

# rich_markup_mode=None: without it, Typer's Rich-based help renderer emits
# ANSI color codes -- inserted *inside* option names (e.g. "--user-key"
# becomes several separately-colored spans) whenever Rich's terminal
# detection decides the output stream is color-capable. That detection is
# environment-dependent (observed: plain on macOS/local, colored on Ubuntu
# CI for the identical piped-subprocess call), so a --help consumer doing a
# plain substring/grep match -- a real MCP client, an operator's shell
# script, or this package's own test_cli_contract.py -- can't rely on it.
app = typer.Typer(add_completion=False, rich_markup_mode=None)


def _version_callback(show_version: bool) -> None:
    if show_version:
        typer.echo(f"band-mcp {__version__}")
        raise typer.Exit()


@app.command(
    help="Band MCP Server - Connect AI agents to Band platform",
    epilog=_EPILOG,
)
def main(
    user_key: Annotated[str | None, typer.Option("--user-key")] = None,
    agent_key: Annotated[str | None, typer.Option("--agent-key")] = None,
    room_id: Annotated[str | None, typer.Option("--room-id")] = None,
    scope: Annotated[
        list[str] | None,
        typer.Option(
            "--scope",
            help=(
                f"Scope to serve. Repeatable or comma-separated. "
                f"Values: {_SCOPE_VALUES}. Default: {_SCOPE_DEFAULT}."
            ),
        ),
    ] = None,
    tools: Annotated[
        list[str] | None,
        typer.Option(
            "--tools",
            help=(
                f"Opt-in tool groups. Repeatable or comma-separated. "
                f"Values: {_TOOLS_VALUES}. Default: {_TOOLS_DEFAULT}. "
                "Note: operators who relied on implicit contacts tools must now "
                "pass --tools contacts."
            ),
        ),
    ] = None,
    transport: Annotated[
        Transport | None,
        typer.Option(
            "--transport", "-t", help="Transport mode: stdio (default) or sse"
        ),
    ] = None,
    host: Annotated[
        str | None,
        typer.Option("--host", help="Host to bind for SSE mode (default: 127.0.0.1)"),
    ] = None,
    port: Annotated[
        int | None,
        typer.Option("--port", "-p", help="Port to bind for SSE mode (default: 8000)"),
    ] = None,
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show version and exit",
        ),
    ] = None,
) -> None:
    """Run the MCP server with configurable transport mode."""
    cli: CliArgs = {
        "user_key": user_key,
        "agent_key": agent_key,
        "room_id": room_id,
        "scope": scope,
        "tools": tools,
    }
    try:
        config = _resolve_validated_config(cli)
    except ConfigError as exc:
        _exit_on_config_error(exc)

    resolver = build_standalone_resolver(config)
    try:
        spec = standalone_spec(config, resolver)
    except ConfigError as exc:
        _exit_on_config_error(exc)

    # Determine transport mode (CLI overrides env) before building the
    # engine: the DNS-rebinding warning below must judge the transport
    # actually started with, not just the env-var default.
    resolved_transport: Transport = transport or settings.transport

    mcp = build_engine(
        spec, transport_security=_build_transport_security(resolved_transport)
    )
    _register_health_check_tool(mcp, resolver)

    logger.info("Starting band-mcp-server v%s", __version__)
    logger.info("Base URL: %s", settings.band_base_url)
    logger.info("Resolved scope: %s", config.scope or "<none>")
    logger.info("Resolved tools: %s", config.tools or "<none>")
    if config.room_id:
        logger.info("Pinned room id: %s", config.room_id)

    if host is not None:
        mcp.settings.host = host
    if port is not None:
        mcp.settings.port = port

    _run_transport(mcp, resolved_transport, host, port)


def run() -> None:
    app()


if __name__ == "__main__":
    run()
