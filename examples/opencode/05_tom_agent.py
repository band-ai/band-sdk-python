# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[opencode]", "mcp>=1.25.0"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""Tom the cat, powered by OpenCode.

Start this alongside ``06_jerry_agent.py``, then add both agents to the same
Band room for a light-weight multi-agent conversation.

Run with:
    uv run examples/opencode/05_tom_agent.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from prompts.characters import generate_tom_prompt  # pyrefly: ignore[missing-import]

from setup_logging import setup_logging  # pyrefly: ignore[missing-import]
from settings import OpenCodeExampleSettings  # pyrefly: ignore[missing-import]
from band import Agent
from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
from band.core.types import AdapterFeatures, Emit

setup_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    settings = OpenCodeExampleSettings()
    adapter = OpencodeAdapter(
        config=OpencodeAdapterConfig(
            base_url=settings.opencode_base_url,
            provider_id=settings.opencode_provider_id,
            model_id=settings.opencode_model_id,
            agent=settings.opencode_agent,
            approval_mode=settings.opencode_approval_mode,
            custom_section=generate_tom_prompt("Tom"),
        ),
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )

    agent = Agent.from_config(
        "tom_agent",
        adapter=adapter,
        ws_url=settings.band_ws_url,
        rest_url=settings.band_rest_url,
    )

    logger.info("Tom is on the prowl, looking for Jerry")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
