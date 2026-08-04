# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[strands]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Strands agent with a fully custom system prompt.

Two levers shape the prompt:

* ``custom_section`` (see 01_basic_agent.py) is appended to the prompt the SDK
  renders, so the Band tool contract stays in place;
* ``system_prompt`` REPLACES that rendered prompt entirely — nothing about the
  platform tools is injected for you, so the prompt below states the messaging
  contract itself. Without it the model answers in plain text, the reply never
  reaches the room, and the adapter reports a dropped-reply error.

Run with:
    uv run examples/strands/03_custom_instructions.py
"""

from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv
from strands.models.openai import OpenAIModel

from setup_logging import setup_logging
from band import Agent
from band.adapters import StrandsAdapter
from band.core.types import AdapterFeatures, Emit

setup_logging()
logger = logging.getLogger(__name__)

SUPPORT_PROMPT = """
You are a technical support agent for a software company, working inside a Band
chat room.

How to reply:
- Every reply to the room MUST go through the band_send_message tool, mentioning
  the person you are answering. Plain text answers never reach the room.
- Send exactly one message per turn.

Guidelines:
- Ask for the environment (OS, version, exact error) before troubleshooting.
- Give numbered, verifiable steps.
- Escalate to a human when the issue needs account or billing access.
"""


async def main() -> None:
    load_dotenv()

    adapter = StrandsAdapter(
        model=OpenAIModel(model_id="gpt-5.4-mini"),
        # Full override: custom_section would be ignored alongside this.
        system_prompt=SUPPORT_PROMPT,
        # Post each tool call and result into the room for visibility.
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )

    agent = Agent.from_config(
        "strands_agent",
        adapter=adapter,
    )

    logger.info("Starting Strands support agent...")
    async with agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
