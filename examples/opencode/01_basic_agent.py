# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[opencode]", "mcp>=1.25.0"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Basic OpenCode adapter agent example.

Prerequisites:
1. Install OpenCode: `npm install -g opencode-ai`
2. Start the server: `opencode serve --hostname=127.0.0.1 --port=4096`
3. Set `BAND_WS_URL` and `BAND_REST_URL`
4. Add agent credentials to `agent_config.yaml`
5. The example defaults to the locally available free model `opencode/mimo-v2.5-free`

Run with:
    uv run examples/opencode/01_basic_agent.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

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
