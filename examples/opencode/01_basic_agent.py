# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[opencode]", "python-dotenv"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Basic OpenCode adapter agent example.

Prerequisites:
1. Install OpenCode: `npm install -g opencode-ai`
2. Start the server: `opencode serve --hostname=127.0.0.1 --port=4096`
3. Set `BAND_WS_URL` and `BAND_REST_URL`
4. Add agent credentials to `agent_config.yaml`
5. The example defaults to the locally available free model `opencode/minimax-m2.5-free`

Run with:
    uv run examples/opencode/01_basic_agent.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from setup_logging import setup_logging  # pyrefly: ignore[missing-import]
from band import Agent
from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
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

    agent_key = os.getenv("AGENT_KEY", "darter")
    adapter = OpencodeAdapter(
        config=OpencodeAdapterConfig(
            base_url=os.getenv("OPENCODE_BASE_URL", "http://127.0.0.1:4096"),
            provider_id=os.getenv("OPENCODE_PROVIDER_ID", "opencode"),
            model_id=os.getenv("OPENCODE_MODEL_ID", "minimax-m2.5-free"),
            agent=os.getenv("OPENCODE_AGENT") or None,
            custom_section="You are a helpful assistant. Keep replies concise.",
            approval_mode=os.getenv("OPENCODE_APPROVAL_MODE", "manual"),  # type: ignore[arg-type]  # env var is str; invalid values fall through to manual mode
            features=AdapterFeatures(emit={Emit.EXECUTION}),
        )
    )

    agent = Agent.from_config(
        agent_key,
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting OpenCode agent: %s", agent_key)
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
