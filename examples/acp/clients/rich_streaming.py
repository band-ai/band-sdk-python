# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[acp]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
ACP Client with rich streaming - Thoughts, tool calls, and plans.

This example shows how the ACPClientAdapter handles rich session_update
chunks from remote ACP agents. Beyond plain text, it captures:

  - Thoughts: Internal reasoning from the agent
  - Tool calls: Tool invocations with name, args, and results
  - Plans: Task plans with status tracking

All rich events are posted back to the Band platform with full type
fidelity, so other participants can see exactly what the remote agent
is doing.

Permission requests from the ACP agent are also posted to the platform
as visible events (auto-allowed by default).

Architecture:
    Band Platform (message arrives in room)
      -> ACPClientAdapter.on_message()
        -> remote ACP prompt/session handling
          -> Remote ACP Agent (e.g., Claude Code)
            -> session_update: thought -> tools.send_event("thought")
            -> session_update: tool_call -> tools.send_event("tool_call")
            -> session_update: text -> tools.send_message()
            -> request_permission -> tools.send_event("tool_call", permission)
        -> All events visible on Band platform

Prerequisites:
    1. Set environment variables:
       - BAND_WS_URL: WebSocket URL
       - BAND_REST_URL: REST API URL
       - ACP_AGENT_COMMAND: Command to spawn
         (default: "npx @zed-industries/codex-acp")

    2. Have the remote ACP agent installed and available in PATH

Run with:
    uv run examples/acp/clients/rich_streaming.py
"""

from __future__ import annotations

import asyncio
import logging
import shlex

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

from band import Agent, configure_logging
from band.adapters import ACPClientAdapter
from band.config import load_agent_config

configure_logging(
    level=logging.DEBUG,
    root_level=logging.DEBUG,
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


async def main() -> None:
    load_dotenv()
    settings = Settings()

    # Load agent credentials from agent_config.yaml
    agent_id, api_key = load_agent_config("acp_client_agent")

    # Command to spawn the remote ACP agent
    acp_command = shlex.split(settings.acp_agent_command)

    # Working directory for ACP sessions
    acp_cwd = settings.acp_agent_cwd

    # Create adapter pointing to remote ACP agent
    adapter = ACPClientAdapter(
        command=acp_command,
        cwd=acp_cwd,
    )

    logger.info("Starting ACP client bridge with rich streaming...")
    logger.info("Command: %s", " ".join(acp_command))
    logger.info("Thoughts, tool calls, and plans will be posted to the platform.")
    async with Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
