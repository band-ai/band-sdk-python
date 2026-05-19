# /// script
# requires-python = ">=3.11"
# dependencies = ["thenvoi-sdk[a2a]"]
#
# [tool.uv.sources]
# thenvoi-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Mixed-example bridge launcher.

Starts two Thenvoi bridge agents in one process:
- one bridge for the external contract checker A2A service
- one bridge for the external risk reviewer A2A service

This is the piece that makes both external A2A services show up as normal,
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

from setup_logging import setup_logging
from thenvoi import Agent
from thenvoi.adapters import A2AAdapter

logger = logging.getLogger(__name__)
CONFIG_PATH = Path(__file__).with_name("agents.yaml")


def _load_platform_urls() -> tuple[str, str]:
    """Load Thenvoi URLs, defaulting to the hosted platform."""
    ws_url = os.getenv(
        "THENVOI_WS_URL", "wss://app.thenvoi.com/api/v1/socket/websocket"
    )
    rest_url = os.getenv("THENVOI_REST_URL", "https://app.thenvoi.com")

    return ws_url, rest_url


def _build_bridge_agent(
    *,
    config_name: str,
    remote_url: str,
    ws_url: str,
    rest_url: str,
) -> Agent:
    """Create one Thenvoi bridge agent for a remote A2A service."""
    adapter = A2AAdapter(remote_url=remote_url, streaming=True)

    return Agent.from_config(
        config_name,
        config_path=CONFIG_PATH,
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )


async def main() -> None:
    setup_logging()
    load_dotenv()

    ws_url, rest_url = _load_platform_urls()
    fact_url = os.getenv("MIXED_FACT_URL", "http://127.0.0.1:10121")
    risk_url = os.getenv("MIXED_RISK_URL", "http://127.0.0.1:10122")

    fact_bridge = _build_bridge_agent(
        config_name="mixed_fact_bridge_agent",
        remote_url=fact_url,
        ws_url=ws_url,
        rest_url=rest_url,
    )
    risk_bridge = _build_bridge_agent(
        config_name="mixed_risk_bridge_agent",
        remote_url=risk_url,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting mixed bridge for contract checker at %s", fact_url)
    logger.info("Starting mixed bridge for risk reviewer at %s", risk_url)

    await asyncio.gather(
        fact_bridge.run(),
        risk_bridge.run(),
    )


if __name__ == "__main__":
    asyncio.run(main())
