# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[acp]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Host-side Band SDK client for the colocated Copilot Docker example.

Connects over TCP to the Copilot ACP server published by the single container
(localhost:8080) and tells Copilot to reach Band tools at the band-mcp server
running on the container's own loopback (127.0.0.1:3000/sse). Band tools are NOT
injected via the SDK's localhost MCP server (`inject_band_tools=False`); the URL
is resolved by Copilot inside its container.

Run (after `docker run`):
    uv run examples/acp/copilot_docker/colocated/client.py
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

    # Copilot's ACP server, published by the container.
    host = os.getenv("COPILOT_ACP_HOST", "localhost")
    port = int(os.getenv("COPILOT_ACP_PORT", "8080"))
    # The TCP server runs in a container, so this must be a path it can access.
    cwd = os.getenv("COPILOT_ACP_CWD", "/")

    # band-mcp's SSE endpoint as reachable BY COPILOT — the container's own loopback.
    band_mcp_sse_url = os.getenv("BAND_MCP_SSE_URL", "http://127.0.0.1:3000/sse")

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
    logger.info(
        "Copilot will call Band tools at %s (its own loopback)", band_mcp_sse_url
    )
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
