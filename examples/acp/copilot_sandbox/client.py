# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[acp]>=5.0.0"]
# ///
"""
GitHub Copilot in a Docker sandbox (sbx), driven by Band over stdio.

Runs the Copilot CLI inside a Docker **microVM sandbox** and speaks ACP to it over
`sbx exec -i <sandbox> copilot --acp` — the SDK's ordinary stdio transport (no TCP,
no socat). Why this over the container examples:

- **Isolation:** Copilot runs in an isolated microVM with its own filesystem/network.
- **Secret safety:** a host-side proxy injects the GitHub token at the network
  boundary — the token never enters the sandbox (`sbx secret set -g github`).
- **Auditable egress:** a default-deny firewall you can inspect with `sbx policy log`.

Band tools are required for room replies. The sandbox's egress firewall blocks
the SDK host's loopback, so create the sandbox with `band-mcp-kit/` and set
`BAND_MCP_SSE_URL=http://127.0.0.1:3000/sse`, or use a reachable external server.

Prerequisites (one-time, see README): `sbx` installed + `sbx login`, a policy
(`sbx policy init balanced`), a sandbox (`sbx create --name … copilot <workspace>`),
and the GitHub secret (`gh auth token | sbx secret set -g github`).

Run with:
    uv run examples/acp/copilot_sandbox/client.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Self-contained (a deployment artifact): configure logging inline.
from band import Agent, LogSettings
from band.adapters import CopilotACPAdapter, CopilotACPAdapterConfig

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    # The sandbox name you created with `sbx create --name <name> copilot <workspace>`.
    sbx_sandbox: str = "copilot-band"
    # A cwd that exists INSIDE the sandbox for each ACP session, resolved against
    # the host cwd (with sbx's default direct mount, the same path applies inside).
    sbx_workspace: str = "."
    band_mcp_sse_url: str = Field(min_length=1)


async def main() -> None:
    load_dotenv()
    LogSettings().for_application().configure()
    settings = Settings()

    sandbox = settings.sbx_sandbox
    workspace = os.path.abspath(settings.sbx_workspace)
    mcp_servers = [
        {
            "type": "sse",
            "name": "band",
            "url": settings.band_mcp_sse_url,
            "headers": [],
        }
    ]

    config = CopilotACPAdapterConfig(
        # Drive Copilot's ACP server inside the sandbox over stdio. `-i` (no `-t`)
        # keeps STDIN open with raw pipes — byte-clean for ACP's NDJSON.
        command=("sbx", "exec", "-i", sandbox, "copilot", "--acp"),
        cwd=workspace,
        # Auth is handled by sbx's host-side secret proxy, not the subprocess env,
        # so no github_token here.
        inject_band_tools=False,  # sandbox egress blocks host loopback; see README
        mcp_servers=mcp_servers,
    )
    adapter = CopilotACPAdapter(config)

    logger.info("Driving Copilot in sandbox %r over stdio (sbx exec -i)...", sandbox)
    logger.info("Copilot will call Band tools at %s", settings.band_mcp_sse_url)
    async with Agent.from_config(
        "copilot_acp_agent",
        adapter=adapter,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
