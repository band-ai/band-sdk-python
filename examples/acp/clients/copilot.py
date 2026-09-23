# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[acp]>=1.2.0"]
# ///
"""
GitHub Copilot CLI ACP Client - Use GitHub Copilot from Band.

Spawns the GitHub Copilot CLI's ACP server (`copilot --acp`) as a subprocess and
bridges it to the Band platform. Messages from Band rooms are forwarded to
Copilot, and Copilot's responses (streaming text, thoughts, tool calls) are
posted back to the room. Band tools are injected through a local, localhost-only
MCP server (HTTP/SSE) that Copilot calls over ACP.

Copilot speaks vanilla ACP (no `copilot/*` extension methods), so no custom
client profile is needed.

Architecture:
    Band Platform (message arrives in room)
      -> CopilotACPAdapter
        -> `copilot --acp` subprocess
          -> Copilot CLI (with Band MCP tools injected)
            -> session_update responses streamed back
        -> Posts response to Band room

Prerequisites:
    1. GitHub Copilot CLI installed and on PATH:
       https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli

    2. A Copilot-entitled GitHub token in the environment (Copilot checks
       COPILOT_GITHUB_TOKEN, then GH_TOKEN, then GITHUB_TOKEN):
       export GITHUB_TOKEN=...

    3. Set environment variables:
       - BAND_API_KEY: Your Band API key (required for tool injection)

    4. Optionally configure:
       - ACP_AGENT_CWD: Working directory for Copilot sessions (default: .)

Run with:
    uv run examples/acp/clients/copilot.py
"""

from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

from band import Agent, configure_logging, create_room_workspace_resolver
from band.adapters import CopilotACPAdapter, CopilotACPAdapterConfig

configure_logging(
    level=logging.INFO,
    root_level=logging.INFO,
    extra_loggers={
        "httpcore": logging.WARNING,
        "httpx": logging.WARNING,
    },
)
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    acp_agent_cwd: str = "."
    github_token: str = ""


async def main() -> None:
    load_dotenv()
    settings = Settings()

    config = CopilotACPAdapterConfig(
        workspace_for_room=create_room_workspace_resolver(settings.acp_agent_cwd),
        github_token=settings.github_token or None,
        inject_band_tools=True,
    )
    adapter = CopilotACPAdapter(config)

    logger.info("Starting GitHub Copilot ACP client bridge...")
    logger.info("Messages from Band will be forwarded to Copilot.")
    async with Agent.from_config(
        "copilot_acp_agent",
        adapter=adapter,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
