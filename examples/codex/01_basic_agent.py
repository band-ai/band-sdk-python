# /// script
# requires-python = ">=3.11"
# dependencies = ["thenvoi-sdk[codex]"]
#
# [tool.uv.sources]
# thenvoi-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Basic Codex adapter agent example.

Runs a Thenvoi agent backed by Codex app-server.

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
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

from setup_logging import setup_logging
from thenvoi import Agent
from thenvoi.adapters.codex import CodexAdapter, CodexAdapterConfig

setup_logging()
logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


async def main() -> None:
    load_dotenv()

    ws_url = os.getenv("THENVOI_WS_URL")
    rest_url = os.getenv("THENVOI_REST_URL")

    if not ws_url:
        raise ValueError("THENVOI_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("THENVOI_REST_URL environment variable is required")

    codex_bin = shutil.which("codex")
    if codex_bin is None:
        logger.error(
            "Codex CLI not found on PATH. Install it: npm install -g @openai/codex"
        )
        sys.exit(1)

    login_check = subprocess.run(
        [codex_bin, "login", "status"],
        capture_output=True,
        text=True,
    )
    if login_check.returncode != 0:
        print("Codex is not logged in.")
        try:
            answer = input("Run 'codex login' now? [Y/n] ").strip().lower()
        except EOFError:
            print("Non-interactive shell. Run 'codex login' manually, then retry.")
            sys.exit(1)
        if answer in ("", "y", "yes"):
            result = subprocess.run([codex_bin, "login"], check=False)
            if result.returncode != 0:
                print("Login failed. Check the output above and retry.")
                sys.exit(1)
        else:
            print("Exiting. Run 'codex login' manually, then retry.")
            sys.exit(1)

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
            enable_task_events=True,
            emit_turn_task_markers=_env_bool("CODEX_TURN_TASK_MARKERS", False),
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
        "Starting Codex agent: agent_key=%s transport=%s role=%s",
        agent_key,
        codex_transport,
        codex_role or "none",
    )
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
