# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[acp]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git", branch = "dev" }
# ///
"""
Host-side Band SDK client for the Copilot Docker Compose example.

Connects over TCP to the Copilot ACP server published by the compose stack
(localhost:8080) and tells Copilot to reach Band tools at the band-mcp service's
SSE endpoint (band-mcp:3000/sse). Because Copilot is remote, Band tools are NOT
injected via the SDK's localhost MCP server (`inject_band_tools=False`); the URL
is resolved by Copilot inside the compose network, not by this host process.

Run (after `docker compose up`):
    uv run examples/acp/copilot_docker/compose/client.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv

from band import Agent
from band.adapters import CopilotACPAdapter, CopilotACPAdapterConfig

# Self-contained: unlike the top-level examples, this deployment artifact does not
# reach a sibling setup_logging helper (no sys.path surgery) — it configures its own.
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()

    ws_url = os.getenv("BAND_WS_URL", "wss://app.band.ai/api/v1/socket/websocket")
    rest_url = os.getenv("BAND_REST_URL", "https://app.band.ai")

    # Copilot's ACP server, published by the compose stack.
    host = os.getenv("COPILOT_ACP_HOST", "localhost")
    port = int(os.getenv("COPILOT_ACP_PORT", "8080"))
    # The TCP server runs in a container, so this must be a path it can access.
    cwd = os.getenv("COPILOT_ACP_CWD", "/")

    # band-mcp's SSE endpoint as reachable BY COPILOT (compose DNS), not by us.
    band_mcp_sse_url = os.getenv("BAND_MCP_SSE_URL", "http://band-mcp:3000/sse")

    config = CopilotACPAdapterConfig(
        host=host,
        port=port,
        cwd=cwd,
        inject_band_tools=False,  # Copilot is remote; it can't reach our loopback MCP
        mcp_servers=[
            {"type": "sse", "name": "band", "url": band_mcp_sse_url, "headers": []}
        ],
        rest_url=rest_url,
    )
    adapter = CopilotACPAdapter(config)

    agent = Agent.from_config(
        "copilot_acp_agent",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Connecting to Copilot ACP server at %s:%s over TCP...", host, port)
    logger.info("Copilot will call Band tools at %s", band_mcp_sse_url)
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
