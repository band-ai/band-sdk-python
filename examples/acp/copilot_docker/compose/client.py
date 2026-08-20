# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[acp]>=1.2.0,<2.0.0"]
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
from pathlib import Path

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
    """Host client + band-mcp shared settings (field name == env var)."""

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
    # Same api_key as copilot_acp_agent — band-mcp authenticates with this.
    # Resolved as process env over .env, matching how compose interpolates
    # ${BAND_AGENT_KEY} for the band-mcp service, so both see one value.
    band_agent_key: str
    copilot_acp_host: str = "localhost"
    copilot_acp_port: int = 8080
    # Path inside the Copilot container (not on the host).
    copilot_acp_cwd: str = "/"
    # SSE URL as reachable BY COPILOT (compose DNS), not by this host process.
    band_mcp_sse_url: str = "http://band-mcp:3000/sse"


async def main() -> None:
    settings = Settings()
    agent_id, api_key = load_agent_config("copilot_acp_agent")
    if settings.band_agent_key != api_key:
        raise ValueError(
            "BAND_AGENT_KEY must match copilot_acp_agent in agent_config.yaml"
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
