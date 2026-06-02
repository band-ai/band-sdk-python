"""CLI entry point for band-acp server."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

logger = logging.getLogger(__name__)


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        args: Optional argument list (defaults to sys.argv).

    Returns:
        Parsed arguments namespace.
    """
    parser = argparse.ArgumentParser(
        prog="band-acp",
        description="Band ACP server — expose Band peers as an ACP agent.",
    )
    parser.add_argument(
        "--agent-id",
        default=os.environ.get("BAND_AGENT_ID"),
        help="Band agent ID (env: BAND_AGENT_ID)",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("BAND_API_KEY"),
        help="Band API key (env: BAND_API_KEY)",
    )
    parser.add_argument(
        "--rest-url",
        default=os.environ.get("BAND_REST_URL", "https://app.band.ai"),
        help="Band REST API URL (default: https://app.band.ai)",
    )
    parser.add_argument(
        "--ws-url",
        default=os.environ.get(
            "BAND_WS_URL",
            "wss://app.band.ai/api/v1/socket/websocket",
        ),
        help="Band WebSocket URL",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )

    return parser.parse_args(args)


async def main(args: argparse.Namespace | None = None) -> None:
    """Run the band-acp server.

    Args:
        args: Parsed arguments. If None, parses from sys.argv.
    """
    if args is None:
        args = parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level))

    if not args.agent_id:
        raise ValueError("Agent ID is required. Use --agent-id or set BAND_AGENT_ID.")
    if not args.api_key:
        raise ValueError("API key is required. Use --api-key or set BAND_API_KEY.")

    # Lazy imports to avoid import errors when ACP deps are not installed
    from acp import run_agent

    from band import Agent
    from band.integrations.acp.push_handler import ACPPushHandler
    from band.integrations.acp.server import ACPServer
    from band.integrations.acp.server_adapter import BandACPServerAdapter

    adapter = BandACPServerAdapter(
        rest_url=args.rest_url,
        api_key=args.api_key,
    )

    # Wire up push handler
    push_handler = ACPPushHandler(adapter)
    adapter.set_push_handler(push_handler)

    server = ACPServer(adapter)

    agent = Agent.create(
        adapter=adapter,
        agent_id=args.agent_id,
        api_key=args.api_key,
        rest_url=args.rest_url,
        ws_url=args.ws_url,
    )

    logger.info("Starting band-acp server (agent_id=%s)", args.agent_id)

    # Start Band agent in background, run ACP server in foreground
    async with agent:
        try:
            await run_agent(server)
        finally:
            await adapter.close()


def entry_point() -> None:
    """CLI entry point for the band-acp command."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except ValueError as e:
        logger.error("Error: %s", e)
        sys.exit(1)
