# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[acp]", "pydantic-settings", "python-dotenv"]
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

Run (after the container is up — see README):
    uv run examples/acp/copilot_docker/colocated/client.py
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from dotenv import dotenv_values
from pydantic_settings import BaseSettings, SettingsConfigDict

from band import Agent
from band.adapters import CopilotACPAdapter, CopilotACPAdapterConfig
from band.config import load_agent_config

# Self-contained: unlike the top-level examples, this deployment artifact does not
# reach a sibling setup_logging helper (no sys.path surgery) — it configures its own.
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

_ENV_FILE = Path(__file__).resolve().parent / ".env"


class Settings(BaseSettings):
    """Host client settings (field name == env var)."""

    model_config = SettingsConfigDict(
        # Layered like the old load_dotenv() walk-up: a cwd .env (e.g. repo
        # root) applies first, the example's own .env wins on conflicts.
        env_file=(".env", _ENV_FILE),
        env_ignore_empty=True,
        extra="ignore",
        case_sensitive=False,
    )

    band_ws_url: str = "wss://app.band.ai/api/v1/socket/websocket"
    band_rest_url: str = "https://app.band.ai"
    copilot_acp_host: str = "localhost"
    copilot_acp_port: int = 8080
    # Path inside the Copilot container (not on the host).
    copilot_acp_cwd: str = "/"
    # SSE URL as reachable BY COPILOT (container loopback).
    band_mcp_sse_url: str = "http://127.0.0.1:3000/sse"


async def main() -> None:
    settings = Settings()
    agent_id, api_key = load_agent_config("copilot_acp_agent")
    # Check the .env file itself: `docker run --env-file .env` gave band-mcp
    # exactly that file, so a shell-exported BAND_AGENT_KEY must not satisfy
    # the shared-identity check on the container's behalf.
    container_key = dotenv_values(_ENV_FILE).get("BAND_AGENT_KEY")
    if container_key != api_key:
        raise ValueError(
            "BAND_AGENT_KEY in .env must match copilot_acp_agent in agent_config.yaml"
        )

    config = CopilotACPAdapterConfig(
        host=settings.copilot_acp_host,
        port=settings.copilot_acp_port,
        cwd=settings.copilot_acp_cwd,
        inject_band_tools=False,  # Copilot is remote; it can't reach our loopback MCP
        mcp_servers=[
            {
                "type": "sse",
                "name": "band",
                "url": settings.band_mcp_sse_url,
                "headers": [],
            }
        ],
        rest_url=settings.band_rest_url,
    )
    adapter = CopilotACPAdapter(config)

    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=settings.band_ws_url,
        rest_url=settings.band_rest_url,
    )

    logger.info(
        "Connecting to Copilot ACP server at %s:%s over TCP...",
        settings.copilot_acp_host,
        settings.copilot_acp_port,
    )
    logger.info("Copilot will call Band tools at %s", settings.band_mcp_sse_url)
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
