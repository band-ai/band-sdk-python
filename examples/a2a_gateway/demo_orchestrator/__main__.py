"""Entry point for the Demo Orchestrator A2A server.

Usage:
    uv run python examples/a2a_gateway/demo_orchestrator/__main__.py
    uv run python examples/a2a_gateway/demo_orchestrator/__main__.py --port 10001
    uv run python examples/a2a_gateway/demo_orchestrator/__main__.py --gateway-url http://localhost:10000

This starts an A2A-compliant server that:
1. Exposes itself at /.well-known/agent.json
2. Accepts messages at /v1/message:stream
3. Routes requests to Band peers via the A2A Gateway
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

# Add parent directory to path for direct script execution
sys.path.insert(0, str(Path(__file__).parent))

import click
import uvicorn
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import (
    InMemoryPushNotificationConfigStore,
    InMemoryTaskStore,
)
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from dotenv import load_dotenv

from agent import OrchestratorAgent
from agent_executor import OrchestratorAgentExecutor
from remote_agent import GatewayClient

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@click.command()
@click.option("--host", default="localhost", help="Host to bind to")
@click.option("--port", default=10001, help="Port to bind to")
@click.option(
    "--gateway-url",
    default=os.getenv("GATEWAY_URL", "http://localhost:10000"),
    help="URL of the A2A Gateway",
)
@click.option(
    "--peers",
    default=os.getenv("AVAILABLE_PEERS", ""),
    help="Comma-separated list of available peer IDs",
)
@click.option(
    "--model",
    default=os.getenv("OPENAI_MODEL", "gpt-4o"),
    help="OpenAI model to use",
)
def main(host: str, port: int, gateway_url: str, peers: str, model: str) -> None:
    """Start the Demo Orchestrator A2A server."""
    # Check for OpenAI API key
    if not os.getenv("OPENAI_API_KEY"):
        logger.error("OPENAI_API_KEY environment variable is required")
        sys.exit(1)

    # Parse available peers from CLI arg
    available_peers = [p.strip() for p in peers.split(",") if p.strip()]

    # Auto-discover peers from gateway if none specified
    if not available_peers:
        logger.info("Discovering peers from gateway at %s...", gateway_url)
        try:
            client = GatewayClient(gateway_url)
            peers_data = asyncio.run(client.list_peers())
            # Use slugs (not UUIDs) - LLM-friendly identifiers
            available_peers = [p["slug"] for p in peers_data]
            asyncio.run(client.close())
            if available_peers:
                logger.info("Discovered %s peers from gateway", len(available_peers))
            else:
                logger.warning("No peers discovered from gateway")
        except Exception as e:
            logger.warning("Could not discover peers from gateway: %s", e)

    logger.info("Starting Demo Orchestrator Agent on %s:%s", host, port)
    logger.info("Gateway URL: %s", gateway_url)
    if available_peers:
        peers_summary = ", ".join(available_peers[:5])
        suffix = "..." if len(available_peers) > 5 else ""
        logger.info(
            "Available peers (%s): %s%s", len(available_peers), peers_summary, suffix
        )
    else:
        logger.warning(
            "No peers available - orchestrator will have limited functionality"
        )

    try:
        # Create the orchestrator agent
        agent = OrchestratorAgent(
            gateway_url=gateway_url,
            available_peers=available_peers,
            model=model,
        )

        # Define agent capabilities
        capabilities = AgentCapabilities(streaming=True, push_notifications=False)

        # Define agent skill
        skill = AgentSkill(
            id="orchestrate_peers",
            name="Peer Orchestration",
            description="Routes requests to specialized Band platform peers via A2A Gateway",
            tags=["orchestration", "routing", "multi-agent"],
            examples=[
                "Ask the weather agent about conditions in NYC",
                "Create a ticket using the ServiceNow agent",
                "Get data analysis from the data analyst peer",
            ],
        )

        # Create agent card
        agent_card = AgentCard(
            name="Demo Orchestrator",
            description=(
                "An orchestrator agent that routes user requests to specialized "
                "Band platform peers via the A2A Gateway. It intelligently "
                "determines which peer can best handle each request."
            ),
            url=f"http://{host}:{port}/",
            version="1.0.0",
            default_input_modes=OrchestratorAgent.SUPPORTED_CONTENT_TYPES,
            default_output_modes=OrchestratorAgent.SUPPORTED_CONTENT_TYPES,
            capabilities=capabilities,
            skills=[skill],
        )

        # Set up A2A server
        push_config_store = InMemoryPushNotificationConfigStore()

        request_handler = DefaultRequestHandler(
            agent_executor=OrchestratorAgentExecutor(agent),
            task_store=InMemoryTaskStore(),
            push_config_store=push_config_store,
        )

        server = A2AStarletteApplication(
            agent_card=agent_card,
            http_handler=request_handler,
        )

        # Run server
        uvicorn.run(server.build(), host=host, port=port)

    except Exception as e:
        logger.error("Error starting server: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
