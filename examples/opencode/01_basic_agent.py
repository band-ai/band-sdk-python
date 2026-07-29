# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[opencode]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Basic OpenCode adapter agent example.

Prerequisites (see `examples/opencode/README.md` for the full setup):
1. Install OpenCode: `npm install -g opencode-ai`
2. Give the server a provider key: the default provider is OpenCode Zen, whose
   models are hosted and need a Zen API key
3. Start the server from an empty throwaway directory, so a small model answers
   instead of exploring a checkout:
   `cd "$(mktemp -d)" && opencode serve --hostname=127.0.0.1 --port=4096`
4. Set `BAND_WS_URL` and `BAND_REST_URL`
5. Add agent credentials to `agent_config.yaml`

Run with:
    uv run examples/opencode/01_basic_agent.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from setup_logging import setup_logging
from settings import OpenCodeExampleSettings
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
            custom_section="You are a helpful assistant. Keep replies concise.",
            approval_mode=settings.opencode_approval_mode,
        ),
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )

    agent = Agent.from_config(
        settings.agent_key,
        adapter=adapter,
        ws_url=settings.band_ws_url,
        rest_url=settings.band_rest_url,
    )

    logger.info("Starting OpenCode agent: %s", settings.agent_key)
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
