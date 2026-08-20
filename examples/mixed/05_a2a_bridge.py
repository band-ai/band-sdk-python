# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[a2a]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Mixed-example bridge launcher.

Starts two Band bridge agents in one process:
- one bridge for the remote contract checker A2A service
- one bridge for the remote risk reviewer A2A service

This is the piece that makes both remote A2A services show up as normal,
bidirectional participants in the shared engineering review room.

Run with:
    uv run examples/mixed/05_a2a_bridge.py
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from band import Agent, configure_logging
from band.adapters import A2AAdapter

logger = logging.getLogger(__name__)
CONFIG_PATH = Path(__file__).with_name("agents.yaml")


def _build_bridge_agent(*, config_name: str, remote_url: str) -> Agent:
    """Create one Band bridge agent for a remote A2A service."""
    adapter = A2AAdapter(remote_url=remote_url, streaming=True)

    return Agent.from_config(
        config_name,
        config_path=CONFIG_PATH,
        adapter=adapter,
    )


async def main() -> None:
    configure_logging(logging.INFO, extra_loggers={"band_crewai_agent": logging.INFO})
    load_dotenv()

    fact_url = os.getenv("MIXED_FACT_URL", "http://127.0.0.1:10121")
    risk_url = os.getenv("MIXED_RISK_URL", "http://127.0.0.1:10122")

    fact_bridge = _build_bridge_agent(
        config_name="mixed_fact_bridge_agent",
        remote_url=fact_url,
    )
    risk_bridge = _build_bridge_agent(
        config_name="mixed_risk_bridge_agent",
        remote_url=risk_url,
    )

    logger.info("Starting mixed bridge for contract checker at %s", fact_url)
    logger.info("Starting mixed bridge for risk reviewer at %s", risk_url)

    await asyncio.gather(
        fact_bridge.run(),
        risk_bridge.run(),
    )


if __name__ == "__main__":
    asyncio.run(main())
