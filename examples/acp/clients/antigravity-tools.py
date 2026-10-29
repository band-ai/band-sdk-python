# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[acp]>=7.0.0"]
# [tool.uv.sources]
# band-sdk = { path = "../../.." }
# ///
"""Combine an application tool with the shared Band task board.

Try: "Use digest to hash 'hello', create a Band task containing the digest,
mark your work completed, and read back its details and history."
Run: uv run examples/acp/clients/antigravity-tools.py
"""

from __future__ import annotations

import asyncio
import hashlib
import logging

from antigravitysettings import Settings, build_config
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from band import Agent, configure_logging
from band.adapters.antigravity_acp import AntigravityACPAdapter
from band.core.types import Capability, Emit
from band.runtime.custom_tools import get_custom_tool_name

logger = logging.getLogger(__name__)


class DigestInput(BaseModel):
    """Calculate the SHA256 digest of UTF-8 text."""

    text: str = Field(min_length=1, description="Text to hash exactly as supplied")


TASK_INSTRUCTIONS = (
    f"Use {get_custom_tool_name(DigestInput)} for SHA256 calculations. "
    "Use Band task-board tools to "
    "track shared work when requested, and read back the result before reporting "
    "success. Native agent tasks are separate from the shared Band task board."
)


def digest(input: DigestInput) -> str:
    """Return a SHA256 digest without a network call or native shell command."""
    return hashlib.sha256(input.text.encode("utf-8")).hexdigest()


def build_adapter(settings: Settings) -> AntigravityACPAdapter:
    """Expose the application tool and opt into the shared task board."""
    return AntigravityACPAdapter(
        build_config(settings, custom_section=TASK_INSTRUCTIONS),
        additional_tools=[(DigestInput, digest)],
        capabilities=Capability.TASKS,
        emit=Emit.TOOL_CALLS,
    )


async def main() -> None:
    """Run the task-board example until interrupted."""
    load_dotenv(".env.test")
    settings = Settings()
    adapter = build_adapter(settings)
    configure_logging()
    logger.info("Starting Antigravity with a custom tool and the Band task board")
    async with Agent.from_config("antigravity_tools_agent", adapter=adapter) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
