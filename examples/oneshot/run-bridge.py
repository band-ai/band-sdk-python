# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[bridge_agentcore]==6.0.1"]
# ///
"""Local launcher for the dumb-pipe bridge with HTTP or AgentCore agents.

Reads :class:`BridgeConfig` from the ``BAND_BRIDGE_AGENTS`` env var (a
JSON array of ``{agent_id, api_key, target}`` entries) and runs the bridge.

Each agent's ``target`` may be either:

- ``{"type": "http", "url": "https://..."}`` — plain HTTP POST
- ``{"type": "agentcore", "runtime_arn": "arn:...", "region": "us-east-1"}``
  — Bedrock AgentCore Runtime via SigV4

Run from the repo root::

    BAND_BRIDGE_AGENTS='[
      {"agent_id":"...","api_key":"...","target":{"type":"agentcore","runtime_arn":"arn:...","region":"us-east-1"}}
    ]' \\
        uv run --isolated examples/oneshot/run-bridge.py

Or place the JSON in ``.env.test`` as a single line.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "band-bridge"))

from bridge_core.bridge import main


def run() -> None:
    """Load the checkout's shared configuration and run its bridge."""
    load_dotenv(REPO_ROOT / ".env.test")
    asyncio.run(main())


if __name__ == "__main__":
    run()
