# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[a2a_gateway_demo]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Run A2A Gateway with Demo Orchestrator Agent.

This example demonstrates end-to-end agent-to-agent communication:
1. A2A Gateway connects to Band platform and exposes peers as A2A endpoints
2. Demo Orchestrator Agent receives user requests and routes them to peers via A2A

Architecture:
    User → Demo Orchestrator (port 10001) → A2A Gateway (port 10000) → Band → Peer
                                          ↑                                        ↓
                                          ←←←←←←←←←←← SSE Response ←←←←←←←←←←←←←←←

Run with:
    uv run examples/a2a_gateway/02_with_demo_agent.py

This will start:
- A2A Gateway on port 10000 (connects to Band, exposes peers)
- Demo Orchestrator on port 10001 (calls gateway peers via A2A protocol)

Prerequisites:
    1. Configure gateway credentials:
       - preferred: gateway_agent in agent_config.yaml
       - fallback: BAND_API_KEY and optional BAND_AGENT_ID
       - BAND_WS_URL: WebSocket URL (default: wss://app.band.ai/api/v1/socket/websocket)
       - BAND_REST_URL: REST API URL (default: https://app.band.ai)
       - OPENAI_API_KEY: OpenAI API key for the orchestrator

    2. Have peers configured on the Band platform

Test the demo:
    # Check orchestrator agent card
    curl http://localhost:10001/.well-known/agent-card.json

    # Send a JSON-RPC message to the orchestrator (it will route to gateway peers)
    curl -X POST http://localhost:10001/ \\
        -H "Content-Type: application/json" \\
        -H "A2A-Version: 1.0" \\
        -d '{
            "jsonrpc": "2.0",
            "id": "1",
            "method": "SendMessage",
            "params": {
                "message": {
                    "role": "ROLE_USER",
                    "parts": [{"text": "Ask the weather peer about NYC"}],
                    "messageId": "msg-1",
                    "contextId": "ctx-1"
                }
            }
        }'
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from pathlib import Path

# Add current directory to path for local imports
sys.path.insert(0, str(Path(__file__).parent))

import uvicorn
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.server.routes.rest_routes import create_rest_routes
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import (
    InMemoryPushNotificationConfigStore,
    InMemoryTaskStore,
)
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.applications import Starlette

from demo_orchestrator.agent import OrchestratorAgent
from demo_orchestrator.agent_executor import OrchestratorAgentExecutor
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
load_dotenv()

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    gateway_port: int = 10000
    orchestrator_port: int = 10001
    # Fallback credentials, used only when agent_config.yaml has no
    # gateway_agent entry.
    band_api_key: str = ""
    band_agent_id: str = "a2a-gateway"


class OrchestratorSettings(BaseSettings):
    """Only constructed by run_orchestrator(); run_gateway() needs none of this."""

    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    openai_api_key: str
    openai_model: str = "gpt-5.4-mini"
    # Comma-separated peer handles (e.g. "weather,translator").
    available_peers: str = ""


settings = Settings()

# Configuration
GATEWAY_HOST = "localhost"
GATEWAY_PORT = settings.gateway_port
ORCHESTRATOR_HOST = "localhost"
ORCHESTRATOR_PORT = settings.orchestrator_port


def _load_gateway_credentials() -> tuple[str, str]:
    """Load gateway credentials from env or agent_config.yaml."""
    try:
        return load_agent_config("gateway_agent")
    except Exception as exc:
        if settings.band_api_key:
            return settings.band_agent_id, settings.band_api_key
        raise ValueError(
            "Configure 'gateway_agent' in agent_config.yaml, or set "
            "BAND_API_KEY and BAND_AGENT_ID environment variables"
        ) from exc


async def run_gateway() -> None:
    """Run the A2A Gateway that exposes Band peers."""
    agent_id, api_key = _load_gateway_credentials()

    gateway_url = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}"

    adapter = A2AGatewayAdapter(
        gateway_url=gateway_url,
        port=GATEWAY_PORT,
    )

    logger.info("Starting A2A Gateway on %s...", gateway_url)
    async with Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
    ) as agent:
        await agent.run_forever()


def run_orchestrator() -> None:
    """Run the Demo Orchestrator that calls gateway peers."""
    orchestrator_settings = OrchestratorSettings()

    gateway_url = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}"
    available_peers = [
        p.strip() for p in orchestrator_settings.available_peers.split(",") if p.strip()
    ]

    # Create orchestrator agent
    agent = OrchestratorAgent(
        gateway_url=gateway_url,
        available_peers=available_peers,
        model=orchestrator_settings.openai_model,
    )

    # Define agent capabilities and card
    capabilities = AgentCapabilities(streaming=True, push_notifications=False)
    skill = AgentSkill(
        id="orchestrate_peers",
        name="Peer Orchestration",
        description="Routes requests to Band platform peers via A2A Gateway",
        tags=["orchestration", "routing"],
        examples=["Ask the weather peer about NYC conditions"],
    )

    agent_card = AgentCard(
        name="Demo Orchestrator",
        description="Routes user requests to Band platform peers via A2A Gateway",
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                protocol_version="1.0",
                url=f"http://{ORCHESTRATOR_HOST}:{ORCHESTRATOR_PORT}/",
            )
        ],
        version="1.0.0",
        default_input_modes=OrchestratorAgent.SUPPORTED_CONTENT_TYPES,
        default_output_modes=OrchestratorAgent.SUPPORTED_CONTENT_TYPES,
        capabilities=capabilities,
        skills=[skill],
    )

    # Set up A2A server
    request_handler = DefaultRequestHandler(
        agent_executor=OrchestratorAgentExecutor(agent),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
        push_config_store=InMemoryPushNotificationConfigStore(),
    )

    server = Starlette(
        routes=(
            create_agent_card_routes(agent_card)
            + create_jsonrpc_routes(
                request_handler,
                rpc_url="/",
                enable_v0_3_compat=True,
            )
            + create_rest_routes(request_handler, enable_v0_3_compat=True)
        )
    )

    logger.info(
        "Starting Demo Orchestrator on http://%s:%s...",
        ORCHESTRATOR_HOST,
        ORCHESTRATOR_PORT,
    )

    # Run uvicorn (blocking)
    uvicorn.run(server, host=ORCHESTRATOR_HOST, port=ORCHESTRATOR_PORT)


async def main() -> None:
    """Run both gateway and orchestrator concurrently."""
    _load_gateway_credentials()
    OrchestratorSettings()

    logger.info("=" * 60)
    logger.info("A2A Gateway + Demo Orchestrator Example")
    logger.info("=" * 60)
    logger.info("")
    logger.info("This example runs:")
    logger.info("  1. A2A Gateway on port %s (exposes Band peers)", GATEWAY_PORT)
    logger.info(
        "  2. Demo Orchestrator on port %s (calls gateway peers)", ORCHESTRATOR_PORT
    )
    logger.info("")
    logger.info("Test with:")
    logger.info(
        "  curl http://localhost:%s/.well-known/agent-card.json", ORCHESTRATOR_PORT
    )
    logger.info("")

    # Run gateway in background, orchestrator in foreground
    # Note: uvicorn.run() is blocking, so we run orchestrator in a thread
    # Start gateway in asyncio
    gateway_task = asyncio.create_task(run_gateway())

    # Wait a bit for gateway to start
    await asyncio.sleep(2)

    # Run orchestrator in a separate thread (uvicorn is blocking)
    orchestrator_thread = threading.Thread(target=run_orchestrator, daemon=True)
    orchestrator_thread.start()

    # Wait for gateway task
    try:
        await gateway_task
    except asyncio.CancelledError:
        logger.info("Shutting down...")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
