# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[opencode]", "mcp>=1.25.0"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""OpenCode coding agent scoped to one local workspace.

Set ``OPENCODE_DIRECTORY`` to the repository OpenCode may inspect or change.
``OPENCODE_WORKSPACE`` is optional and selects an OpenCode workspace when the
server has more than one configured.

The default approval mode is ``manual``. When OpenCode asks to run a command
or modify a file, reply in the Band room with ``approve``, ``always``, or
``reject``. Do not use ``auto_accept`` for a workspace you do not trust.

Run with:
    OPENCODE_DIRECTORY=/absolute/path/to/project \
      uv run examples/opencode/02_workspace_agent.py
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
    if not settings.opencode_directory:
        raise ValueError("OPENCODE_DIRECTORY environment variable is required")

    adapter = OpencodeAdapter(
        config=OpencodeAdapterConfig(
            base_url=settings.opencode_base_url,
            directory=settings.opencode_directory,
            workspace=settings.opencode_workspace,
            provider_id=settings.opencode_provider_id,
            model_id=settings.opencode_model_id,
            agent=settings.opencode_agent,
            approval_mode=settings.opencode_approval_mode,
            custom_section=(
                "You are a careful coding assistant. Inspect the workspace before "
                "proposing changes, explain the change and its verification, and do "
                "not modify files or run commands without the user's approval."
            ),
        ),
        features=AdapterFeatures(emit={Emit.EXECUTION, Emit.TASK_EVENTS}),
    )

    agent = Agent.from_config(
        settings.agent_key,
        adapter=adapter,
        ws_url=settings.band_ws_url,
        rest_url=settings.band_rest_url,
    )

    logger.info("Starting workspace agent for %s", settings.opencode_directory)
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
