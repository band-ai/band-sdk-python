# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[opencode]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""OpenCode coding agent scoped to one local workspace.

Set ``OPENCODE_DIRECTORY`` to the absolute path of the repository OpenCode may
inspect or change. ``OPENCODE_WORKSPACE`` is optional and is forwarded to the
server as its workspace selector (the ``x-opencode-workspace`` header).

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


from settings import OpenCodeExampleSettings
from band import Agent, configure_logging
from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
from band.core.types import AdapterFeatures, Emit

configure_logging(
    level=logging.INFO,
    stream="stdout",
    root_level=logging.INFO,
    extra_loggers={"httpx": logging.WARNING},
)
logger = logging.getLogger(__name__)


async def main() -> None:
    settings = OpenCodeExampleSettings()
    if not settings.opencode_directory:
        raise ValueError("OPENCODE_DIRECTORY environment variable is required")
    # The server resolves the directory itself, so a relative path would point
    # at the server's working directory rather than the caller's.
    if not os.path.isabs(settings.opencode_directory):
        raise ValueError("OPENCODE_DIRECTORY must be an absolute path")

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

    logger.info("Starting workspace agent for %s", settings.opencode_directory)
    async with Agent.from_config(
        settings.agent_key,
        adapter=adapter,
        ws_url=settings.band_ws_url,
        rest_url=settings.band_rest_url,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
