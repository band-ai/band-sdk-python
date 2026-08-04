# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[opencode]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""OpenCode agent with durable Band memory.

Try prompts like:
- "Remember that I prefer concise status updates."
- "What do you remember about my update style?"

Run with:
    uv run examples/opencode/04_memory_secretary.py
"""

from __future__ import annotations

import asyncio
import logging


from settings import OpenCodeExampleSettings
from band import Agent, configure_logging
from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
from band.core.types import Capability, Emit

configure_logging(
    level=logging.INFO,
    stream="stdout",
    root_level=logging.INFO,
    extra_loggers={"httpx": logging.WARNING},
)
logger = logging.getLogger(__name__)

MEMORY_INSTRUCTIONS = (
    "You are a personal secretary who preserves useful long-term context. "
    "Store durable preferences, profile facts, standing instructions, important "
    "project facts, and reusable workflows with Band memory tools before replying. "
    "Do not store one-off requests, temporary chat context, or sensitive information "
    "unless the user clearly asks you to remember it. Search memory before answering "
    "questions about prior preferences or facts. Keep responses short."
)


async def main() -> None:
    settings = OpenCodeExampleSettings()
    adapter = OpencodeAdapter(
        config=OpencodeAdapterConfig(
            base_url=settings.opencode_base_url,
            provider_id=settings.opencode_provider_id,
            model_id=settings.opencode_model_id,
            agent=settings.opencode_agent,
            approval_mode=settings.opencode_approval_mode,
            custom_section=MEMORY_INSTRUCTIONS,
        ),
        capabilities=Capability.MEMORY,
        emit=Emit.TOOL_CALLS,
    )

    logger.info("Starting OpenCode memory secretary")
    async with Agent.from_config(
        settings.agent_key,
        adapter=adapter,
        ws_url=settings.band_ws_url,
        rest_url=settings.band_rest_url,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
