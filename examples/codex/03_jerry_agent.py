# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[codex,logging]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Jerry the mouse agent - clever and cheese-loving!

This example shows how to create a character agent with a custom personality
using the Codex adapter.

The character prompt is loaded from a shared prompts module that can be
reused across different adapter implementations.

Run with (from repo root):
    uv run examples/codex/03_jerry_agent.py

Note: Must be run from repo root as it imports prompts/characters.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys

from dotenv import load_dotenv

# Add parent directory to path for prompts import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from prompts.characters import generate_jerry_prompt

from setup_logging import setup_logging
from band import Agent
from band.adapters.codex import CodexAdapter, CodexAdapterConfig
from band.core.types import AdapterFeatures, Emit

setup_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()

    ws_url = os.getenv("BAND_WS_URL")
    rest_url = os.getenv("BAND_REST_URL")

    if not ws_url:
        raise ValueError("BAND_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("BAND_REST_URL environment variable is required")

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

    adapter = CodexAdapter(
        config=CodexAdapterConfig(
            transport="stdio",
            cwd=os.getenv("CODEX_CWD", os.getcwd()),
            model=os.getenv("CODEX_MODEL") or None,
            personality="none",
            custom_section=generate_jerry_prompt("Jerry"),
            include_base_instructions=True,
            fallback_send_agent_text=True,
        ),
        features=AdapterFeatures(emit={Emit.TASK_EVENTS}),
    )

    agent = Agent.from_config(
        "jerry_agent",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Jerry is cozy in his hole, watching for Tom...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
