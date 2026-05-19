# /// script
# requires-python = ">=3.11"
# dependencies = ["thenvoi-sdk[langgraph]"]
#
# [tool.uv.sources]
# thenvoi-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Thinker agent for the 20 Questions Arena game.

The Thinker picks a secret word from a category (animals, foods, objects,
vehicles), announces the category to the room, invites the Guesser agent,
and answers yes/no questions for up to 20 rounds.

Supports both OpenAI and Anthropic LLMs -- set either OPENAI_API_KEY or
ANTHROPIC_API_KEY in your environment.

Run with (from repo root):
    # Default (auto-detects LLM from env, prefers Anthropic)
    uv run examples/20-questions-arena/thinker_agent.py

    # Explicit model
    uv run examples/20-questions-arena/thinker_agent.py --model claude-sonnet-4-6
    uv run examples/20-questions-arena/thinker_agent.py -m gpt-5.2
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from dotenv import load_dotenv
from langgraph.checkpoint.memory import InMemorySaver

sys.path.insert(0, os.path.dirname(__file__))

from prompts import create_llm, create_llm_by_name, generate_thinker_prompt

from setup_logging import setup_logging
from thenvoi import Agent
from thenvoi.adapters import LangGraphAdapter

logger = logging.getLogger(__name__)

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Thinker agent for 20 Questions Arena")
    parser.add_argument(
        "--model",
        "-m",
        default=None,
        help="LLM model name (e.g. gpt-5.2, claude-sonnet-4-6). "
        "If omitted, auto-detects from env vars.",
    )
    return parser.parse_args()

async def main() -> None:
    load_dotenv()
    args = _parse_args()
    setup_logging(agent_tag="thinker")

    logger.info("=" * 60)
    logger.info("THINKER AGENT STARTING")
    logger.info("  model flag : %s", args.model or "(auto-detect)")
    logger.info("=" * 60)

    ws_url = os.getenv("THENVOI_WS_URL")
    rest_url = os.getenv("THENVOI_REST_URL")

    if not ws_url:
        raise ValueError("THENVOI_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("THENVOI_REST_URL environment variable is required")

    # Load Thinker's credentials from agent_config.yaml    logger.info("  agent_id   : %s", agent_id)
    logger.info("  ws_url     : %s", ws_url)
    logger.info("  rest_url   : %s", rest_url)

    # Select LLM: explicit model name or auto-detect from env
    if args.model:
        llm = create_llm_by_name(args.model)
    else:
        llm = create_llm()
    logger.info("  llm class  : %s", type(llm).__name__)
    logger.info(
        "  llm model  : %s",
        getattr(llm, "model_name", getattr(llm, "model", "unknown")),
    )

    # Create adapter with Thinker's game prompt
    adapter = LangGraphAdapter(
        llm=llm,
        checkpointer=InMemorySaver(),
        custom_section=generate_thinker_prompt("Thinker"),
    )

    # Create and start agent
    agent = Agent.from_config(
        "arena_thinker",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Thinker is ready -- waiting for a user to start a game...")
    await agent.run()

if __name__ == "__main__":
    asyncio.run(main())
