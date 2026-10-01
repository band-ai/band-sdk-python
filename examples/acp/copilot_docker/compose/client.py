# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[acp]>=4.0.0", "pydantic-settings>=2.0.0"]
# ///
"""
Host-side Band SDK client for the Copilot Docker Compose example.

For every Band room the SDK starts its own `copilot --acp` inside the running
`copilot` service (`docker compose exec -T`) and speaks ACP over that process's
stdio. Copilot calls Band tools on the separate band-mcp service
(band-mcp:3000/sse, resolved over the compose network), so the SDK's own loopback
MCP server is not injected (`inject_band_tools=False`).

Run (after `docker compose up -d`):
    uv run examples/acp/copilot_docker/compose/client.py
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from dotenv import load_dotenv
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

_EXAMPLE_DIR = Path(__file__).resolve().parent
_ENV_FILE = _EXAMPLE_DIR / ".env"
_COMPOSE_FILE = _EXAMPLE_DIR / "docker-compose.yml"


class Settings(BaseSettings):
    """Host client + compose shared settings (field name == env var)."""

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
    # The directory compose mounts into the copilot service at the same path.
    copilot_workspaces: Path
    # SSE URL as reachable BY COPILOT (compose DNS), not by this host process.
    band_mcp_sse_url: str = "http://band-mcp:3000/sse"


async def main() -> None:
    load_dotenv()
    settings = Settings()

    _, api_key = load_agent_config("copilot_acp_agent")
    if settings.band_agent_key != api_key:
        raise ValueError(
            "BAND_AGENT_KEY must match copilot_acp_agent in agent_config.yaml"
        )
    workspaces = settings.copilot_workspaces
    if workspaces.resolve() != workspaces:
        # The SDK resolves room paths; compose mounts this one verbatim.
        raise ValueError(
            f"COPILOT_WORKSPACES must be absolute and symlink-free: "
            f"use {workspaces.resolve()}"
        )

    config = CopilotACPAdapterConfig(
        command=(
            "docker",
            "compose",
            "-f",
            str(_COMPOSE_FILE),
            "exec",
            "-T",
            "copilot",
            "copilot",
            "--acp",
            "--allow-all-tools",
        ),
        cwd=str(workspaces),
        inject_band_tools=False,  # Copilot's container can't reach our loopback
        mcp_servers=[
            {
                "type": "sse",
                "name": "band",
                "url": settings.band_mcp_sse_url,
                "headers": [],
            }
        ],
    )
    adapter = CopilotACPAdapter(config)

    logger.info("One copilot --acp per room via docker compose exec over stdio")
    logger.info("Copilot will call Band tools at %s", settings.band_mcp_sse_url)
    async with Agent.from_config(
        "copilot_acp_agent",
        adapter=adapter,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
