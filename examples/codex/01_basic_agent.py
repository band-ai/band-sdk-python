# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[codex]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Basic Codex adapter agent example.

Runs a Band agent backed by the OpenAI Codex Python SDK runtime.

Prerequisites:
1. Band agent credentials in `agent_config.yaml`.
2. Codex authentication through `codex login`, `OPENAI_API_KEY`, or the Codex process environment.

Run:
    uv run examples/codex/01_basic_agent.py

Optional env overrides:
    AGENT_KEY=darter
    CODEX_ROLE=coding|planner|reviewer
    CODEX_MODEL=gpt-5.5
    CODEX_APPROVAL_MODE=manual|auto_accept|auto_decline
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from setup_logging import setup_logging
from band import Agent
from band.adapters.codex import CodexAdapter, CodexAdapterConfig

setup_logging()
logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


async def main() -> None:
    load_dotenv()

    ws_url = os.getenv("BAND_WS_URL")
    rest_url = os.getenv("BAND_REST_URL")

    if not ws_url:
        raise ValueError("BAND_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("BAND_REST_URL environment variable is required")

    agent_key = os.getenv("AGENT_KEY", "darter")

    # Load role prompt from file if CODEX_ROLE is set
    codex_role = os.getenv("CODEX_ROLE")
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

    adapter = CodexAdapter(
        config=CodexAdapterConfig(
            model=os.getenv("CODEX_MODEL") or None,
            cwd=os.getenv("CODEX_CWD", os.getcwd()),
            approval_policy=os.getenv("CODEX_APPROVAL_POLICY", "never"),
            approval_mode=os.getenv("CODEX_APPROVAL_MODE", "manual"),  # type: ignore[arg-type]  # str from env, validated at runtime
            personality="pragmatic",
            custom_section=custom_section,
            include_base_instructions=True,
            enable_task_events=True,
            fallback_send_agent_text=True,
        )
    )

    agent = Agent.from_config(
        agent_key,
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info(
        "Starting Codex agent: agent_key=%s role=%s",
        agent_key,
        codex_role or "none",
    )
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
