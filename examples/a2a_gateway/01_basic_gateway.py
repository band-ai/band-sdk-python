# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[a2a_gateway]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Basic A2A Gateway adapter example.

This example creates a gateway that exposes Band platform peers as A2A
endpoints. Remote A2A-compliant agents can connect to this gateway and
interact with Band peers via standard A2A protocol.

Use Case:
    - You have a remote agent (e.g., SAP Agent) that uses A2A protocol
    - You want that agent to interact with Band platform peers
    - This gateway runs as a sidecar, exposing peers as A2A endpoints

Architecture:
    Remote Agent → A2A HTTP → Gateway → Band REST API → Platform Peers
                  ↑                                              ↓
                  ←←←←←←← SSE Response Stream ←←←←←←←←←←←←←←←←←←←

Features:
    - Automatic peer discovery from Band platform
    - Per-peer A2A endpoints with AgentCard discovery
    - SSE streaming for real-time responses
    - Context management (room-per-context)
    - Session rehydration on restart

Prerequisites:
    1. Configure gateway credentials:
       - preferred: gateway_agent in agent_config.yaml
       - fallback: BAND_API_KEY and optional BAND_AGENT_ID
       - BAND_WS_URL: WebSocket URL (default: wss://app.band.ai/api/v1/socket/websocket)
       - BAND_REST_URL: REST API URL (default: https://app.band.ai)

    2. Have peers configured on the Band platform

Run with:
    uv run examples/a2a_gateway/01_basic_gateway.py

Then remote agents can connect:
    - Discovery: GET http://localhost:10000/agents/weather/.well-known/agent-card.json
    - JSON-RPC:  POST http://localhost:10000/agents/weather
    - Stream:    POST http://localhost:10000/agents/weather/v1/message:stream
"""

from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

from band import Agent, configure_logging
from band.adapters import A2AGatewayAdapter
from band.config import load_agent_config

configure_logging(
    level=logging.INFO,
    root_level=logging.INFO,
    extra_loggers={
        "httpcore": logging.WARNING,
        "httpx": logging.WARNING,
        "uvicorn": logging.WARNING,
    },
)
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    # Fallback credentials, used only when agent_config.yaml has no
    # gateway_agent entry.
    band_api_key: str = ""
    band_agent_id: str = "a2a-gateway"
    gateway_port: int = 10000


async def main() -> None:
    load_dotenv()
    settings = Settings()

    try:
        agent_id, api_key = load_agent_config("gateway_agent")
        logger.info("Loaded gateway credentials from agent_config.yaml")
    except Exception:
        if not settings.band_api_key:
            raise ValueError(
                "Configure 'gateway_agent' in agent_config.yaml, or set "
                "BAND_API_KEY and BAND_AGENT_ID environment variables"
            )
        api_key = settings.band_api_key
        agent_id = settings.band_agent_id
        logger.info("Loaded gateway credentials from environment variables")

    # Create gateway adapter
    # It uses its own REST client for room/message operations
    # gateway_url derives from port (override for a public address)
    adapter = A2AGatewayAdapter(port=settings.gateway_port)

    # Create and start agent
    # The gateway connects to Band and starts its HTTP server

    logger.info("Starting A2A Gateway on %s...", adapter.gateway_url)
    logger.info("Peers will be exposed at:")
    logger.info(
        "  - %s/agents/{peer_id}/.well-known/agent-card.json (discovery)",
        adapter.gateway_url,
    )
    logger.info(
        "  - %s/agents/{peer_id}/v1/message:stream (messaging)", adapter.gateway_url
    )
    logger.info("Waiting for peers to be discovered...")

    async with Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
