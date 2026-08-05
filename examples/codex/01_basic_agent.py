# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[codex,logging]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Basic Codex adapter agent example.

Runs a Band agent backed by Codex app-server.

Prerequisites:
1. OAuth login:
   codex login
2. For stdio mode (default), no extra process is needed.
3. For ws mode, start app-server separately:
   codex app-server --listen ws://127.0.0.1:8765

Run:
    uv run examples/codex/01_basic_agent.py

Optional env overrides:
    AGENT_KEY=darter
    CODEX_TRANSPORT=stdio|ws
    CODEX_WS_URL=ws://127.0.0.1:8765
    CODEX_ROLE=coding|planner|reviewer
    CODEX_MODEL=gpt-5.5
    CODEX_APPROVAL_MODE=manual|auto_accept|auto_decline
    CODEX_TURN_TASK_MARKERS=true|false
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

from band import Agent, configure_logging
from band.adapters.codex import CodexAdapter, CodexAdapterConfig
from band.core.types import Emit

configure_logging(
    level=logging.INFO,
    style="json",
    root_level=logging.INFO,
    stream="stdout",
    extra_loggers={
        "websockets": logging.WARNING,
        "httpx": logging.WARNING,
    },
)
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    agent_key: str = "darter"
    codex_role: str = ""


async def main() -> None:
    load_dotenv()
    settings = Settings()

    agent_key = settings.agent_key

    # Load role prompt from file if CODEX_ROLE is set
    codex_role = settings.codex_role
    custom_section = "You are a helpful assistant. Keep responses concise."
    if codex_role:
        prompt_file = Path(__file__).parent / "prompts" / f"{codex_role}.md"
        if prompt_file.exists():
            custom_section = prompt_file.read_text(encoding="utf-8")
            logger.info("Using role prompt from: %s", prompt_file)
        else:
            logger.warning(
                "Role '%s' specified but no prompt file at %s", codex_role, prompt_file
            )

    # transport/codex_ws_url/model/cwd/approval_policy/approval_mode/
    # emit_turn_task_markers all self-source from CODEX_* env vars (see module
    # docstring) when omitted here.
    adapter = CodexAdapter(
        config=CodexAdapterConfig(
            personality="pragmatic",
            custom_section=custom_section,
            include_base_instructions=True,
            fallback_send_agent_text=True,
        ),
        emit=Emit.TASK_EVENTS,
    )

    logger.info(
        "Starting Codex agent: agent_key=%s role=%s",
        agent_key,
        codex_role or "none",
    )
    async with Agent.from_config(
        agent_key,
        adapter=adapter,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
