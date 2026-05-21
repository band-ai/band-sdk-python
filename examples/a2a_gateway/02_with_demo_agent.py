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
       - fallback: THENVOI_API_KEY and optional THENVOI_AGENT_ID
       - THENVOI_WS_URL: WebSocket URL (default: wss://app.band.ai/api/v1/socket/websocket)
       - THENVOI_REST_URL: REST API URL (default: https://app.band.ai)
       - OPENAI_API_KEY: OpenAI API key for the orchestrator

    2. Have peers configured on the Band platform

Test the demo:
    # Check orchestrator agent card
    curl http://localhost:10001/.well-known/agent.json

    # Send a JSON-RPC message to the orchestrator (it will route to gateway peers)
    curl -X POST http://localhost:10001/ \\
        -H "Content-Type: application/json" \\
        -d '{
            "jsonrpc": "2.0",
            "id": "1",
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": "Ask the weather peer about NYC"}],
                    "messageId": "msg-1",
                    "contextId": "ctx-1"
                }
            }
        }'
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

# Add current directory to path for local imports
sys.path.insert(0, str(Path(__file__).parent))

import uvicorn
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import (
    InMemoryPushNotificationConfigStore,
    InMemoryTaskStore,
)
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from dotenv import load_dotenv

from demo_orchestrator.agent import OrchestratorAgent
from demo_orchestrator.agent_executor import OrchestratorAgentExecutor
from setup_logging import setup_logging
from band import Agent
from band.adapters import A2AGatewayAdapter
from band.config import load_agent_config

setup_logging()
load_dotenv()

logger = logging.getLogger(__name__)

# Configuration
GATEWAY_HOST = "localhost"
GATEWAY_PORT = int(os.getenv("GATEWAY_PORT", "10000"))
ORCHESTRATOR_HOST = "localhost"
ORCHESTRATOR_PORT = int(os.getenv("ORCHESTRATOR_PORT", "10001"))


def _load_gateway_credentials() -> tuple[str, str]:
    """Load gateway credentials from env or agent_config.yaml."""
    try:
        return load_agent_config("gateway_agent")
    except Exception as exc:
        api_key = os.getenv("THENVOI_API_KEY")
        if api_key:
            return os.getenv("THENVOI_AGENT_ID", "a2a-gateway"), api_key
        raise ValueError(
            "Configure 'gateway_agent' in agent_config.yaml, or set "
            "THENVOI_API_KEY and THENVOI_AGENT_ID environment variables"
        ) from exc


def _require_openai_api_key() -> str:
    """Ensure the orchestrator model API key is configured."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "OPENAI_API_KEY environment variable is required for the demo orchestrator"
        )
    return api_key


async def run_gateway() -> None:
    """Run the A2A Gateway that exposes Band peers."""
    ws_url = os.getenv("THENVOI_WS_URL", "wss://app.band.ai/api/v1/socket/websocket")
    rest_url = os.getenv("THENVOI_REST_URL", "https://app.band.ai")
    agent_id, api_key = _load_gateway_credentials()

    gateway_url = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}"

    adapter = A2AGatewayAdapter(
        rest_url=rest_url,
        api_key=api_key,
        gateway_url=gateway_url,
        port=GATEWAY_PORT,
    )

    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting A2A Gateway on %s...", gateway_url)
    await agent.run()


def run_orchestrator() -> None:
    """Run the Demo Orchestrator that calls gateway peers."""
    _require_openai_api_key()

    gateway_url = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}"
    available_peers = os.getenv("AVAILABLE_PEERS", "").split(",")
    available_peers = [p.strip() for p in available_peers if p.strip()]

    # Create orchestrator agent
    agent = OrchestratorAgent(
        gateway_url=gateway_url,
        available_peers=available_peers,
        model=os.getenv("OPENAI_MODEL", "gpt-4o"),
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
        url=f"http://{ORCHESTRATOR_HOST}:{ORCHESTRATOR_PORT}/",
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
        push_config_store=InMemoryPushNotificationConfigStore(),
    )

    server = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )

    logger.info(
        "Starting Demo Orchestrator on http://%s:%s...",
        ORCHESTRATOR_HOST,
        ORCHESTRATOR_PORT,
    )

    # Run uvicorn (blocking)
    uvicorn.run(server.build(), host=ORCHESTRATOR_HOST, port=ORCHESTRATOR_PORT)


async def main() -> None:
    """Run both gateway and orchestrator concurrently."""
    _load_gateway_credentials()
    _require_openai_api_key()

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
    logger.info("  curl http://localhost:%s/.well-known/agent.json", ORCHESTRATOR_PORT)
    logger.info("")

    # Run gateway in background, orchestrator in foreground
    # Note: uvicorn.run() is blocking, so we run orchestrator in a thread
    import threading

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
