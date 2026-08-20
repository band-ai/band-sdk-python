# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[acp]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
ACP Bridge Architecture example.

This example demonstrates the refactored outbound ACP architecture where
Band bridge concerns are separated from generic ACP runtime plumbing.

Architecture:
    Band Platform (message arrives in room)
      -> ACPClientAdapter (Band bridge)
         - room/session mapping
         - bootstrap context + event emission
         - Band MCP tool policy (adapter-level)
      -> ACPRuntime (generic ACP subprocess/session plumbing)
      -> Remote ACP runtime (Codex, Claude Code, Gemini CLI, Cursor, etc.)

Relation to A2A:
    The analogy holds at the bridge boundary: both adapters map Band room
    messages to a remote protocol session and stream responses back.

    The main difference is transport ownership:
    - A2A adapter talks to a remote A2A peer over HTTP/SSE.
    - ACP outbound can spawn a local ACP subprocess and manage its lifecycle.

Prerequisites:
    1. Set BAND_API_KEY in your environment.
    2. Install an ACP-capable runtime (default command uses codex-acp).

Optional environment variables:
    - ACP_AGENT_COMMAND (default: "npx @zed-industries/codex-acp")
    - ACP_AGENT_CWD (default: ".")
    - ACP_AUTH_METHOD (example: "cursor_login")
    - ACP_INJECT_BAND_TOOLS (default: true)

Run with:
    uv run examples/acp/clients/bridge_architecture.py
"""

from __future__ import annotations

import asyncio
import logging
import shlex

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

from band import Agent, configure_logging
from band.adapters import ACPClientAdapter
from band.integrations.acp.client_profiles import CursorACPClientProfile

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

    acp_agent_command: str = "npx @zed-industries/codex-acp"
    acp_agent_cwd: str = "."
    acp_auth_method: str = ""
    acp_inject_band_tools: bool = True
    acp_client_profile: str = ""


async def main() -> None:
    load_dotenv()
    settings = Settings()

    command = shlex.split(settings.acp_agent_command)
    cwd = settings.acp_agent_cwd
    auth_method = settings.acp_auth_method or None
    inject_band_tools = settings.acp_inject_band_tools
    profile_name = settings.acp_client_profile.strip().lower()
    profile = CursorACPClientProfile() if profile_name == "cursor" else None

    adapter = ACPClientAdapter(
        command=command,
        cwd=cwd,
        inject_band_tools=inject_band_tools,
        auth_method=auth_method,
        profile=profile,
    )

    logger.info("Starting ACP bridge architecture example...")
    logger.info("ACP command: %s", " ".join(command))
    logger.info("Band tool injection enabled: %s", inject_band_tools)
    logger.info(
        "ACP client profile: %s",
        type(profile).__name__ if profile else "None",
    )

    async with Agent.from_config(
        "acp_client_agent",
        adapter=adapter,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
