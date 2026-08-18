"""MCP server entry point.

Dual-credential configuration: `--user-key`, `--agent-key`,
`--room-id`, `--scope`, `--tools` CLI flags (plus matching env vars). Tool
registration runs through the SDK-driven registrar (`tools/registrar.py`).

Legacy `BAND_API_KEY` is still supported as a fallback. When it's the only
credential supplied, `config.scope` is rewritten from the key's capabilities
so the advertised tool surface matches what the key can actually call.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import replace
from typing import Literal

from band_mcp import __version__
from band_mcp.config import (
    Config,
    ConfigError,
    _legacy_key_capabilities,
    resolve_config,
    settings,
    validate,
)
from band_mcp.shared import (
    AppContextType,
    get_app_context,
    logger,
    mcp,
    set_pending_config,
)
from band_mcp.tools.registrar import register_tools


@mcp.tool()
async def health_check(ctx: AppContextType) -> str:
    """Test MCP server and API connectivity."""
    app_ctx = get_app_context(ctx)
    checked: list[str] = []
    if app_ctx.human_rest is not None:
        surface = "human"
        try:
            await app_ctx.human_rest.human_api_agents.list_my_agents()
            checked.append(surface)
        except Exception as exc:
            return f"Failed | {surface} | {exc}"
    if app_ctx.agent_rest is not None:
        surface = "agent"
        try:
            await app_ctx.agent_rest.agent_api_identity.get_agent_me()
            checked.append(surface)
        except Exception as exc:
            return f"Failed | {surface} | {exc}"
    if checked:
        return f"OK | {','.join(checked)} | {settings.band_base_url}"
    return "Failed | no credential configured"


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
  BAND_API_KEY          Legacy single-key path (still supported as fallback)
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
        type=str,
        choices=["stdio", "sse"],
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


def _cli_mapping(args: argparse.Namespace) -> dict[str, object]:
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


def _is_pure_legacy_invocation(args: argparse.Namespace, config: Config) -> bool:
    """True when the operator set only BAND_API_KEY and no new flags/envs.

    Used to preserve backward compatibility: an operator who never touched the
    new flags should keep booting even if `validate()` would otherwise fail on
    the default `--scope agent` with no agent credential, as long as the
    legacy key is present and can serve something. Also triggers the scope
    write-back so the advertised surface matches what the legacy key can call.
    """
    if config.legacy_key is None:
        return False
    if any(
        getattr(args, attr) is not None
        for attr in ("user_key", "agent_key", "room_id", "scope", "tools")
    ):
        return False
    new_envs = (
        "BAND_USER_KEY",
        "BAND_AGENT_KEY",
        "BAND_MCP_SCOPE",
        "BAND_MCP_TOOLS",
        "BAND_MCP_ROOM_ID",
    )
    return not any(os.environ.get(name) for name in new_envs)


def run() -> None:
    """Run the MCP server with configurable transport mode.

    Order of operations:
    1. Parse CLI flags.
    2. Resolve the Config (dual-credential + scope/tools/room_id).
    3. Validate; raise ConfigError to exit before FastMCP starts, unless this
       is a pure-legacy (BAND_API_KEY-only) invocation.
    4. Emit every ConfigWarning entry at WARN level.
    5. For pure-legacy invocations, rewrite `config.scope` from the legacy
       key's capabilities so the advertised surface matches.
    6. Hand the Config to the lifespan (so AppContext picks it up).
    7. Register SDK-driven tools.
    8. Start FastMCP.
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
        # Fall back to the pure-legacy path: if BAND_API_KEY is set and the
        # operator supplied no explicit scope/keys, honor the old behavior.
        # This keeps existing deployments booting even when validate() would
        # otherwise complain.
        legacy_human, legacy_agent = _legacy_key_capabilities(config.legacy_key)
        if _is_pure_legacy_invocation(args, config) and (legacy_human or legacy_agent):
            logger.info(
                "Proceeding via legacy BAND_API_KEY path (no new-style "
                "credentials or scope supplied)."
            )
        else:
            logger.error("Configuration error: %s", exc)
            raise SystemExit(2) from exc

    # Escape-hatch scope write-back: when this is a pure-legacy invocation,
    # replace the default scope (["agent"]) with whatever the legacy key
    # actually serves. This keeps the advertised tool surface consistent with
    # the credential's capabilities — a `thnv_u_*` legacy key lands as
    # ["human"], not ["agent"].
    if _is_pure_legacy_invocation(args, config):
        legacy_human, legacy_agent = _legacy_key_capabilities(config.legacy_key)
        legacy_scope: list[Literal["agent", "human"]] = []
        if legacy_agent:
            legacy_scope.append("agent")
        if legacy_human:
            legacy_scope.append("human")
        config = replace(config, scope=legacy_scope)

    set_pending_config(config)

    # SDK-driven registrar: registers every
    # ``iter_tool_definitions(surface=s, ...)`` entry for each scope in
    # ``config.scope``. Single source of truth for tool definitions, shared
    # with the SDK.
    try:
        register_tools(mcp, config)
    except ConfigError as exc:
        # Missing SDK is fatal. Fall out cleanly with exit code 2 so
        # operators see the actionable message instead of a traceback.
        logger.error("Configuration error: %s", exc)
        raise SystemExit(2) from exc

    # Determine transport mode (CLI args override env vars)
    transport: Literal["stdio", "sse"] = args.transport or settings.transport

    if args.host is not None:
        mcp.settings.host = args.host
    if args.port is not None:
        mcp.settings.port = args.port

    logger.info("Starting band-mcp-server v%s", __version__)
    logger.info("Base URL: %s", settings.band_base_url)
    logger.info("Resolved scope: %s", config.scope or "<none>")
    logger.info("Resolved tools: %s", config.tools or "<none>")
    if config.room_id:
        logger.info("Pinned room id: %s", config.room_id)

    if transport == "stdio":
        logger.info("Transport: STDIO (for IDE integration)")
        logger.info("Server ready - listening for MCP protocol messages on STDIO")
        mcp.run(transport="stdio")
    else:
        host = args.host or settings.host
        port = args.port or settings.port
        logger.info("Transport: SSE (HTTP server mode)")
        logger.info("Server ready - listening on http://%s:%s", host, port)
        logger.info("SSE endpoint: /sse | Messages endpoint: /messages/")
        mcp.run(transport="sse")


if __name__ == "__main__":
    run()
