# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "band-sdk[acp]>=4.0.0",
#   "pydantic-settings>=2.0.0",
#   "python-dotenv>=1.2.2",
# ]
# ///
"""
Host-side Band SDK client for the colocated Copilot Docker example.

Every Band room gets its own container: the SDK spawns `docker run -i --rm` of the
example image per room and speaks ACP over that process's stdio. Inside, Copilot
calls Band tools on the band-mcp server sharing its container
(127.0.0.1:3000/sse), so the SDK's own loopback MCP server is not injected
(`inject_band_tools=False`).

Run (after building the image — see README):
    uv run examples/acp/copilot_docker/colocated/client.py
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from dotenv import dotenv_values, load_dotenv
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
    copilot_image: str = "copilot-band-acp"
    # Host directory holding one workspace per room; mounted at the same path in
    # each room's container, so the session cwd exists on both sides.
    copilot_workspaces: Path = _EXAMPLE_DIR / "workspaces"
    # SSE URL as reachable BY COPILOT (its container's loopback).
    band_mcp_sse_url: str = "http://127.0.0.1:3000/sse"


async def main() -> None:
    load_dotenv()
    settings = Settings()

    _, api_key = load_agent_config("copilot_acp_agent")
    # Check the .env file itself: `docker run --env-file .env` gives band-mcp
    # exactly that file, so a shell-exported BAND_AGENT_KEY must not satisfy
    # the shared-identity check on the container's behalf.
    container_key = dotenv_values(_ENV_FILE).get("BAND_AGENT_KEY")
    if container_key != api_key:
        raise ValueError(
            "BAND_AGENT_KEY in .env must match copilot_acp_agent in agent_config.yaml"
        )

    workspaces = settings.copilot_workspaces.resolve()
    config = CopilotACPAdapterConfig(
        command=(
            "docker",
            "run",
            "-i",
            "--rm",
            "--env-file",
            str(_ENV_FILE),
            "-v",
            f"{workspaces}:{workspaces}",
            settings.copilot_image,
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

    logger.info(
        "One %s container per room over stdio; workspaces under %s",
        settings.copilot_image,
        workspaces,
    )
    async with Agent.from_config(
        "copilot_acp_agent",
        adapter=adapter,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
