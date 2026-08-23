# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[codex,logging]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Tom the cat agent - tries to catch Jerry!

This example shows how to create a character agent with a custom personality
using the Codex adapter.

The character prompt is loaded from a shared prompts module that can be
reused across different adapter implementations.

Prerequisites: Codex CLI installed and authenticated (`codex login`).
A missing or unreachable backend fails at startup with instructions.

Run with (from repo root):
    uv run examples/codex/02_tom_agent.py

Note: Must be run from repo root as it imports prompts/characters.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

# Add parent directory to path for prompts import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from prompts.characters import generate_tom_prompt

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


async def main() -> None:
    load_dotenv()

    # cwd/model self-source from CODEX_CWD/CODEX_MODEL when omitted here.
    adapter = CodexAdapter(
        config=CodexAdapterConfig(
            transport="stdio",
            personality="none",
            custom_section=generate_tom_prompt("Tom"),
            include_base_instructions=True,
            fallback_send_agent_text=True,
        ),
        emit={Emit.TASK_EVENTS, Emit.THOUGHTS},
    )

    logger.info("Tom is on the prowl, looking for Jerry...")
    async with Agent.from_config(
        "tom_agent",
        adapter=adapter,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
