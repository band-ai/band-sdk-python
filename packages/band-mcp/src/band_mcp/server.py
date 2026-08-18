"""MCP server entry point.

Dual-credential configuration: `--user-key`, `--agent-key`,
`--room-id`, `--scope`, `--tools` CLI flags (plus matching env vars). Tool
registration builds an ``EngineSpec`` (``standalone_spec``, below) and hands
it to the shared engine (``band.integrations.mcp.engine.build_engine``).
There is no single-key fallback -- a credential is either scope-specific or
absent.
"""

from __future__ import annotations

import argparse
import os

from mcp.server.transport_security import TransportSecuritySettings

from band.integrations.mcp.engine import (
    EngineSpec,
    SendEventWideInput,
    build_engine,
    build_tool_registration,
    extend_with_chat_id,
    pin_existing_chat_id,
)
from band.runtime.tools import (
    EVENT_TOOL_NAMES,
    Surface,
    classify_room_binding,
    iter_tool_definitions,
)

from band_mcp import __version__
from band_mcp.config import (
    CliArgs,
    Config,
    ConfigError,
    ToolGroup,
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
    include_contacts = ToolGroup.CONTACTS in config.tools
    include_memory = ToolGroup.MEMORY in config.tools
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
            include_contacts=include_contacts,
            include_memory=include_memory,
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
                model = pin_existing_chat_id(model, pinned_room_id)

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


async def _health_check(resolver: StandaloneResolver) -> str:
    """Test MCP server and API connectivity.

    A module-level function taking ``resolver`` explicitly (rather than a
    bare ``@mcp.tool()`` closure) so it stays unit-testable in isolation --
    ``run()`` registers a zero-arg wrapper that closes over the real resolver.
    """
    checked: list[str] = []
    if resolver.human_rest is not None:
        surface = "human"
        try:
            await resolver.human_rest.human_api_agents.list_my_agents()
            checked.append(surface)
        except Exception as exc:
            return f"Failed | {surface} | {exc}"
    if resolver.agent_rest is not None:
        surface = "agent"
        try:
            await resolver.agent_rest.agent_api_identity.get_agent_me()
            checked.append(surface)
        except Exception as exc:
            return f"Failed | {surface} | {exc}"
    if checked:
        return f"OK | {','.join(checked)} | {settings.band_base_url}"
    return "Failed | no credential configured"


def _build_transport_security() -> TransportSecuritySettings:
    if (
        settings.transport == "sse"
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Band MCP Server - Connect AI agents to Band platform",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
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
  BAND_MCP_SCOPE        Comma-separated scopes (default: agent)
  BAND_MCP_TOOLS        Opt-in tool groups: contacts, memory
  BAND_MCP_ROOM_ID      Optional pinned room id
  BAND_BASE_URL         Base URL for Band API (default: https://app.band.ai)
  TRANSPORT             Transport mode: stdio or sse (default: stdio)
  HOST                  Host to bind for SSE mode (default: 127.0.0.1)
  PORT                  Port to bind for SSE mode (default: 8000)
        """,
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"band-mcp {__version__}",
    )

    parser.add_argument("--user-key", dest="user_key", type=str, default=None)
    parser.add_argument("--agent-key", dest="agent_key", type=str, default=None)
    parser.add_argument("--room-id", dest="room_id", type=str, default=None)
    parser.add_argument(
        "--scope",
        dest="scope",
        action="append",
        default=None,
        help=(
            "Scope to serve. Repeatable or comma-separated. "
            "Values: agent, human. Default: agent."
        ),
    )
    parser.add_argument(
        "--tools",
        dest="tools",
        action="append",
        default=None,
        help=(
            "Opt-in tool groups. Repeatable or comma-separated. "
            "Values: contacts, memory. Default: none. "
            "Note: operators who relied on implicit contacts tools must now "
            "pass --tools contacts."
        ),
    )

    parser.add_argument(
        "--transport",
        "-t",
        type=Transport,
        choices=list(Transport),
        default=None,
        help="Transport mode: stdio (default) or sse",
    )

    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="Host to bind for SSE mode (default: 127.0.0.1)",
    )

    parser.add_argument(
        "--port",
        "-p",
        type=int,
        default=None,
        help="Port to bind for SSE mode (default: 8000)",
    )

    return parser.parse_args(argv)


def _cli_mapping(args: argparse.Namespace) -> CliArgs:
    """Flatten argparse results into the shape `resolve_config` expects.

    `scope` and `tools` use argparse `action="append"`, so they arrive as
    `list[str] | None`. `_normalize_list_value` in `config.py` handles the
    final trim/split/lowercase/dedupe — we pass the raw list straight through.
    """
    return {
        "user_key": args.user_key,
        "agent_key": args.agent_key,
        "room_id": args.room_id,
        "scope": args.scope,
        "tools": args.tools,
    }


def run() -> None:
    """Run the MCP server with configurable transport mode.

    Order of operations:
    1. Parse CLI flags.
    2. Resolve the Config (dual-credential + scope/tools/room_id).
    3. Validate; raise ConfigError to exit before the engine builds.
    4. Emit every ConfigWarning entry at WARN level.
    5. Build the EngineSpec (standalone_spec) and the engine (build_engine).
    6. Register the health_check tool.
    7. Start the engine over the requested transport.
    """
    args = parse_args()

    config = resolve_config(cli=_cli_mapping(args), env=os.environ)

    # Emit warnings BEFORE validate() — validate might raise and we want the
    # operator to see "did you mean" hints even if config is also missing
    # credentials. Order: did-you-mean first, credentials-missing last.
    for warning in config.warnings:
        logger.warning(warning.message)

    try:
        validate(config)
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        raise SystemExit(2) from exc

    resolver = build_standalone_resolver(config)
    try:
        spec = standalone_spec(config, resolver)
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        raise SystemExit(2) from exc

    mcp = build_engine(spec, transport_security=_build_transport_security())

    # Named health_check directly (not e.g. _health_check_tool): FastMCP
    # derives the advertised schema's "title" from the function's own
    # __name__, independent of the tool() name= override below -- a wrapper
    # named differently would leak into the wire-visible schema title.
    @mcp.tool(name="health_check")
    async def health_check() -> str:
        """Test MCP server and API connectivity."""
        return await _health_check(resolver)

    logger.info("Starting band-mcp-server v%s", __version__)
    logger.info("Base URL: %s", settings.band_base_url)
    logger.info("Resolved scope: %s", config.scope or "<none>")
    logger.info("Resolved tools: %s", config.tools or "<none>")
    if config.room_id:
        logger.info("Pinned room id: %s", config.room_id)

    # Determine transport mode (CLI args override env vars)
    transport: Transport = args.transport or settings.transport

    if args.host is not None:
        mcp.settings.host = args.host
    if args.port is not None:
        mcp.settings.port = args.port

    match transport:
        case Transport.STDIO:
            logger.info("Transport: STDIO (for IDE integration)")
            logger.info("Server ready - listening for MCP protocol messages on STDIO")
            mcp.run(transport="stdio")
        case Transport.SSE:
            host = args.host or settings.host
            port = args.port or settings.port
            logger.info("Transport: SSE (HTTP server mode)")
            logger.info("Server ready - listening on http://%s:%s", host, port)
            logger.info("SSE endpoint: /sse | Messages endpoint: /messages/")
            mcp.run(transport="sse")


if __name__ == "__main__":
    run()
