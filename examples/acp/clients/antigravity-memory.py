# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[acp]>=7.0.0"]
# [tool.uv.sources]
# band-sdk = { path = "../../.." }
# ///
"""Store durable preferences in Band memory and recall them across rooms.

Try: "Remember in Band memory that I prefer concise status updates."
Then ask in another room: "What do you remember about my update style?"
Run: uv run examples/acp/clients/antigravity-memory.py
"""

from __future__ import annotations

import asyncio
import logging

from antigravitysettings import Settings, build_config
from dotenv import load_dotenv

from band import Agent, configure_logging
from band.adapters.antigravity_acp import AntigravityACPAdapter
from band.core.types import Capability, Emit

logger = logging.getLogger(__name__)
MEMORY_INSTRUCTIONS = (
    "Use Band memory for preferences and project facts the user asks you to "
    "remember. Search Band memory before answering questions about saved facts. "
    "Native conversation recall and native memory do not replace Band memory. "
    "Avoid storing secrets or temporary requests."
)


def build_adapter(settings: Settings) -> AntigravityACPAdapter:
    """Opt into durable Band memory while retaining private native sessions."""
    return AntigravityACPAdapter(
        build_config(settings, custom_section=MEMORY_INSTRUCTIONS),
        capabilities=Capability.MEMORY,
        emit=Emit.TOOL_CALLS,
    )


async def main() -> None:
    """Run the memory example until interrupted."""
    load_dotenv(".env.test")
    settings = Settings()
    adapter = build_adapter(settings)
    configure_logging()
    logger.info("Starting Antigravity with durable Band memory")
    async with Agent.from_config("antigravity_memory_agent", adapter=adapter) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
