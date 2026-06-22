"""Local launcher for the dumb-pipe bridge with AgentCore agents.

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
        uv run python examples/agentcore/run_agentcore.py

Or place the JSON in ``.env.test`` as a single line.
"""

from __future__ import annotations

import asyncio
import os
import sys

# Add band-bridge dir to path so bridge_core is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "band-bridge"))

from dotenv import load_dotenv

load_dotenv(
    os.environ.get(
        "ENV_FILE", os.path.join(os.path.dirname(__file__), "..", "..", ".env.test")
    )
)

from bridge_core.bridge import main  # noqa: E402

asyncio.run(main())
