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
import os
from pathlib import Path

from dotenv import load_dotenv

from band import Agent, configure_logging
from band.adapters.codex import CodexAdapter, CodexAdapterConfig
from band.core.types import AdapterFeatures, Emit

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


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


async def main() -> None:
    load_dotenv()

    agent_key = os.getenv("AGENT_KEY", "darter")
    codex_transport = os.getenv("CODEX_TRANSPORT", "stdio")
    if codex_transport not in {"stdio", "ws"}:
        raise ValueError("CODEX_TRANSPORT must be 'stdio' or 'ws'")

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
            transport=codex_transport,  # type: ignore[arg-type]  # str from env, validated at runtime
            codex_ws_url=os.getenv("CODEX_WS_URL", "ws://127.0.0.1:8765"),
            model=os.getenv("CODEX_MODEL") or None,
            cwd=os.getenv("CODEX_CWD", os.getcwd()),
            approval_policy=os.getenv("CODEX_APPROVAL_POLICY", "never"),
            approval_mode=os.getenv("CODEX_APPROVAL_MODE", "manual"),  # type: ignore[arg-type]  # str from env, validated at runtime
            personality="pragmatic",
            custom_section=custom_section,
            include_base_instructions=True,
            emit_turn_task_markers=_env_bool("CODEX_TURN_TASK_MARKERS", False),
            fallback_send_agent_text=True,
        ),
        features=AdapterFeatures(emit={Emit.TASK_EVENTS}),
    )

    logger.info(
        "Starting Codex agent: agent_key=%s transport=%s role=%s",
        agent_key,
        codex_transport,
        codex_role or "none",
    )
    async with Agent.from_config(
        agent_key,
        adapter=adapter,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
